"""The one place a model call is reported to the tracing backend.

A ``GENERATION`` observation is what makes a trace answer the questions tracing
exists for: which model ran, how many tokens it used, what it cost. The
vocabulary that produces one is identical for every runtime -- only the numbers
differ, and every runtime obtains them from its own protocol. So the vocabulary
lives here and a runtime supplies values.

Keeping it here is not tidiness for its own sake. `langfuse.observation.type`
decides whether Langfuse renders the observation as a generation at all, and the
token table is read by three consumers under three names (OpenTelemetry's GenAI
convention, this platform's own, Langfuse's `usage_details` JSON). A second copy
of either is a runtime that reports tokens the backend never looks at -- which is
exactly what happened while a Claude-shaped copy was the only one.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self

from harness.core.manifest import AgentManifestSnapshot
from harness.observability.provider import AttributeValue, Observability

MODEL_SPAN_NAME = "harness.model.run"

# One row per counter: what a runtime calls it, the attribute it is reported
# under, and what Langfuse's usage-details object calls it. The attribute names
# are the ones already published: OpenTelemetry's GenAI convention covers
# prompt/completion tokens, and this platform names the cache counters itself.
_USAGE_ATTRIBUTES: tuple[tuple[str, str, str], ...] = (
    ("input_tokens", "gen_ai.usage.input_tokens", "input"),
    ("output_tokens", "gen_ai.usage.output_tokens", "output"),
    (
        "cache_creation_input_tokens",
        "harness.usage.cache_creation_input_tokens",
        "cache_creation_input",
    ),
    (
        "cache_read_input_tokens",
        "harness.usage.cache_read_input_tokens",
        "cache_read_input",
    ),
)


@dataclass(frozen=True)
class ModelRunFacts:
    """What is already known about the model call before it starts."""

    run_id: str
    agent_name: str
    agent_version: str
    content_hash: str
    route_id: str
    model: str
    provider: str
    policy_profile: str
    skill_count: int
    # The SDK permission mode is a Claude concept; a runtime without one omits
    # the attribute rather than inventing a value for it.
    permission_mode: str | None = None
    package_hash: str | None = None

    def attributes(self) -> dict[str, AttributeValue]:
        values: dict[str, AttributeValue] = {
            "run.id": self.run_id,
            "agent.name": self.agent_name,
            "agent.version": self.agent_version,
            "agent.content_hash": self.content_hash,
            "gen_ai.operation.name": "chat",
            "gen_ai.provider.name": self.provider,
            "gen_ai.request.model": self.model,
            "langfuse.observation.type": "generation",
            "langfuse.observation.model.name": self.model,
            "langfuse.observation.metadata.provider": self.provider,
            "langfuse.observation.metadata.route_id": self.route_id,
            "langfuse.version": self.agent_version,
            "harness.model.route": self.route_id,
            "harness.policy.profile": self.policy_profile,
            "harness.skill.count": self.skill_count,
        }
        if self.permission_mode is not None:
            values["harness.model.permission_mode"] = self.permission_mode
        if self.package_hash is not None:
            values["agent.package_hash"] = self.package_hash
        return values


def model_run_facts(
    snapshot: AgentManifestSnapshot,
    *,
    run_id: str,
    route_id: str,
    model: str,
    provider: str,
    permission_mode: str | None = None,
    package_hash: str | None = None,
) -> ModelRunFacts:
    """Derive a model run's facts from the published Agent.

    One derivation for every runtime, so two runtimes cannot describe the same
    Agent differently in the same trace.
    """

    manifest = snapshot.manifest
    return ModelRunFacts(
        run_id=run_id,
        agent_name=manifest.metadata.name,
        agent_version=manifest.metadata.version,
        content_hash=snapshot.content_hash,
        route_id=route_id,
        model=model,
        provider=provider,
        policy_profile=manifest.spec.permissions.policy,
        skill_count=len(snapshot.skill_snapshots),
        permission_mode=permission_mode,
        package_hash=package_hash,
    )


class ModelObservation(AbstractContextManager["ModelObservation"]):
    """A live ``harness.model.run`` observation, open while the runtime works.

    The runtime's own loop is what decides when the model call is over, so the
    observation is entered and exited by the runtime and its result is reported
    with whatever that runtime's protocol reported. Every field of
    :meth:`record_result` is optional: a runtime that does not learn a value
    leaves it out, which reads as "not reported" rather than as a zero.
    """

    def __init__(
        self,
        observability: Observability | None,
        facts: ModelRunFacts,
        *,
        input_value: object | None = None,
    ) -> None:
        self._observability = observability
        self._facts = facts
        self._input_value = input_value
        self._span: AbstractContextManager[None] = nullcontext()

    def __enter__(self) -> Self:
        if self._observability is not None:
            self._span = self._observability.span(
                MODEL_SPAN_NAME,
                attributes=self._facts.attributes(),
            )
        self._span.__enter__()
        if self._input_value is not None:
            self.annotate_io(input_value=self._input_value)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        # The span is started with `set_status_on_exception=False`, so a failure
        # that escapes the runtime is marked here or not at all.
        if exc_type is not None and self._observability is not None:
            self._observability.mark_current_span_error(exc_type.__name__)
        return self._span.__exit__(exc_type, exc, traceback)

    def annotate_io(
        self,
        *,
        input_value: object | None = None,
        output_value: object | None = None,
        trace_level: bool = False,
    ) -> None:
        """Record content on the observation, if content capture is enabled."""

        if self._observability is None:
            return
        self._observability.annotate_current_io(
            input_value=input_value,
            output_value=output_value,
            trace_level=trace_level,
        )

    def record_result(
        self,
        *,
        usage: Mapping[str, int] | None = None,
        cost_usd: float | None = None,
        duration_ms: int | None = None,
        api_duration_ms: int | None = None,
        turns: int | None = None,
        stop_reason: str | None = None,
        output: object | None = None,
        is_error: bool = False,
        status_message: str | None = None,
    ) -> None:
        """Report what the call cost, then label the observation.

        The output goes to the trace level as well, so a Langfuse trace shows the
        answer without opening the generation -- and only the runtime knows the
        final text, which is why it is reported here rather than by the Worker.
        """

        observability = self._observability
        if observability is None:
            return
        attributes: dict[str, AttributeValue] = {
            "harness.model.is_error": is_error,
            "langfuse.observation.level": "ERROR" if is_error else "DEFAULT",
            "langfuse.observation.status_message": status_message
            or ("模型处理失败" if is_error else "模型处理完成"),
        }
        for name, value in (
            ("harness.model.duration_ms", duration_ms),
            ("harness.model.api_duration_ms", api_duration_ms),
            ("harness.model.turns", turns),
            ("harness.model.stop_reason", stop_reason),
        ):
            if value is not None:
                attributes[name] = value
        if cost_usd is not None:
            attributes["harness.model.cost_usd"] = cost_usd
            attributes["langfuse.observation.cost_details"] = json.dumps(
                {"total": cost_usd}, separators=(",", ":")
            )
        usage_details: dict[str, int] = {}
        for source, attribute_name, langfuse_name in _USAGE_ATTRIBUTES:
            if usage is None or source not in usage:
                continue
            attributes[attribute_name] = usage[source]
            usage_details[langfuse_name] = usage[source]
        if usage_details:
            attributes["langfuse.observation.usage_details"] = json.dumps(
                usage_details, separators=(",", ":")
            )
        observability.annotate_current_span(attributes)
        if output is not None:
            self.annotate_io(output_value=output)
            self.annotate_io(output_value=output, trace_level=True)

    def annotate(self, attributes: Mapping[str, AttributeValue]) -> None:
        """Attach runtime-specific attributes to the observation."""

        if self._observability is not None:
            self._observability.annotate_current_span(attributes)

    def mark_error(self, error_type: str) -> None:
        """Mark the observation failed without closing it."""

        if self._observability is not None:
            self._observability.mark_current_span_error(error_type)


def model_observation(
    observability: Observability | None,
    facts: ModelRunFacts,
    *,
    input_value: Any = None,
) -> ModelObservation:
    """Open a model observation; a no-op one when tracing is not configured."""

    return ModelObservation(observability, facts, input_value=input_value)
