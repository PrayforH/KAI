"""Fail-closed governance for the SDK's one-level Lead/Sub execution graph."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, cast

from harness.core.manifest import AgentManifestSnapshot
from harness.core.models import AgentVersion
from harness.observability.provider import Observability
from harness.runtime.base import RuntimeEvent

SUBAGENT_EVENT_SCHEMA = "harness.subagent.v1"
_TERMINAL_TYPES = frozenset({"subagent.completed", "subagent.failed"})
_RUNTIME_TASK_TYPES = {
    "runtime.task.started": "subagent.started",
    "runtime.task.updated": "subagent.updated",
    "runtime.task.completed": "subagent.completed",
    "runtime.task.failed": "subagent.failed",
}


class SubagentGovernanceError(RuntimeError):
    """Raised when SDK delegation violates the immutable Lead/Sub contract."""


@dataclass(frozen=True)
class SubagentBinding:
    alias: str
    agent_name: str
    agent_version: str
    policy: str
    timeout_seconds: int | None


@dataclass
class _TaskState:
    task_id: str
    binding: SubagentBinding
    started_at: float
    parent_tool_use_id: str | None


class SubagentRuntimeGovernor:
    """Validate and enrich a single Run's subagent lifecycle events."""

    def __init__(
        self,
        *,
        root: AgentManifestSnapshot,
        subagent_versions: dict[str, AgentVersion],
        observability: Observability | None = None,
    ) -> None:
        self._root = root
        self._limits = root.manifest.spec.limits
        self._observability = observability
        self._bindings: dict[str, SubagentBinding] = {}
        self._tool_aliases: dict[str, str] = {}
        self._tasks: dict[str, _TaskState] = {}
        self._started_task_ids: set[str] = set()
        declared = {
            item.runtime_name: item for item in root.manifest.spec.subagents
        }
        if subagent_versions and set(declared) != set(subagent_versions):
            missing = sorted(set(declared) - set(subagent_versions))
            extra = sorted(set(subagent_versions) - set(declared))
            raise SubagentGovernanceError(
                "subagent runtime bindings do not match the pinned manifest "
                f"(missing={missing}, extra={extra})"
            )
        if len(declared) > self._limits.max_subagents:
            raise SubagentGovernanceError(
                f"declared subagents exceed maxSubagents={self._limits.max_subagents}"
            )
        for alias, version in subagent_versions.items():
            snapshot = AgentManifestSnapshot.model_validate(version.snapshot)
            if snapshot.manifest.spec.subagents:
                raise SubagentGovernanceError(
                    f"nested delegation is not supported: {alias} declares subagents"
                )
            self._bindings[alias] = SubagentBinding(
                alias=alias,
                agent_name=version.name,
                agent_version=version.version,
                policy=snapshot.manifest.spec.permissions.policy,
                timeout_seconds=snapshot.manifest.spec.limits.timeout_seconds,
            )
        # Direct adapter tests and local embedding may omit the registry resolver.
        # Preserve the immutable manifest identity in that mode; the production
        # RegistryClaudeRuntime always supplies and verifies the published child.
        if not subagent_versions:
            for alias, declaration in declared.items():
                agent_name, separator, agent_version = declaration.ref.rpartition("@")
                if not separator:
                    raise SubagentGovernanceError(
                        f"subagent reference is not pinned: {declaration.ref}"
                    )
                self._bindings[alias] = SubagentBinding(
                    alias=alias,
                    agent_name=agent_name,
                    agent_version=agent_version,
                    policy=root.manifest.spec.permissions.policy,
                    timeout_seconds=self._limits.timeout_seconds,
                )

    @property
    def active_tasks(self) -> tuple[_TaskState, ...]:
        return tuple(self._tasks.values())

    def _alias_from_tool_request(self, event: RuntimeEvent) -> None:
        if event.type != "tool.request" or event.payload.get("name") not in {
            "Task",
            "Agent",
        }:
            return
        arguments = event.payload.get("arguments")
        if not isinstance(arguments, dict):
            return
        values = cast(dict[str, Any], arguments)
        alias = next(
            (
                str(values[key])
                for key in ("subagent_type", "agent", "name")
                if isinstance(values.get(key), str) and values[key]
            ),
            "",
        )
        tool_call_id = event.payload.get("tool_call_id")
        if isinstance(tool_call_id, str):
            self._tool_aliases[tool_call_id] = alias

    def _promote_runtime_task(self, event: RuntimeEvent) -> RuntimeEvent | None:
        """Promote only lifecycle events tied to an actual declared delegation.

        Recent SDK versions also emit Task* system messages for ordinary background
        tools such as Bash. Those are runtime task telemetry, not subagents, and must
        not be forced through the fail-closed Lead/Sub contract.
        """
        subagent_type = _RUNTIME_TASK_TYPES.get(event.type)
        if subagent_type is None:
            return None
        task_id = str(event.payload.get("task_id", ""))
        if event.type == "runtime.task.started":
            parent_tool_use_id = str(
                event.payload.get("parent_tool_use_id", "")
            )
            declared_alias = any(
                isinstance(candidate, str) and candidate in self._bindings
                for candidate in (
                    event.payload.get("alias"),
                    event.payload.get("task_type"),
                )
            )
            linked_delegation = parent_tool_use_id in self._tool_aliases
            if not declared_alias and not linked_delegation:
                return None
        elif task_id not in self._tasks:
            parent_tool_use_id = str(
                event.payload.get("parent_tool_use_id", "")
            )
            if parent_tool_use_id not in self._tool_aliases:
                return None
        return event.model_copy(update={"type": subagent_type})

    def _resolve_alias(self, event: RuntimeEvent) -> str:
        payload = event.payload
        candidates = (
            payload.get("alias"),
            payload.get("task_type"),
            self._tool_aliases.get(str(payload.get("parent_tool_use_id", ""))),
        )
        for candidate in candidates:
            if isinstance(candidate, str) and candidate in self._bindings:
                return candidate
        if any(isinstance(candidate, str) and candidate for candidate in candidates):
            raise SubagentGovernanceError(
                "subagent event references an undeclared role alias"
            )
        if len(self._bindings) == 1:
            return next(iter(self._bindings))
        raise SubagentGovernanceError(
            "subagent event is not bound to a declared role alias"
        )

    def _safe_usage(self, payload: dict[str, Any]) -> dict[str, int]:
        usage = payload.get("usage")
        if not isinstance(usage, dict):
            return {}
        typed_usage = cast(dict[str, object], usage)
        return {
            key: value
            for key in ("total_tokens", "tool_uses", "duration_ms")
            if isinstance((value := typed_usage.get(key)), int)
            and not isinstance(value, bool)
            and value >= 0
        }

    def _enriched(
        self,
        event: RuntimeEvent,
        state: _TaskState,
        *,
        now: float,
    ) -> RuntimeEvent:
        usage = self._safe_usage(event.payload)
        # Preserve measured usage; historical Token quotas do not stop child tasks.
        duration_ms = usage.get(
            "duration_ms", max(0, round((now - state.started_at) * 1000))
        )
        if (
            state.binding.timeout_seconds is not None
            and duration_ms > state.binding.timeout_seconds * 1000
        ):
            raise SubagentGovernanceError(
                f"subagent duration exceeded for alias {state.binding.alias}"
            )
        payload = {
            **event.payload,
            "event_schema": SUBAGENT_EVENT_SCHEMA,
            "alias": state.binding.alias,
            "agent_name": state.binding.agent_name,
            "agent_version": state.binding.agent_version,
            "policy_profile": state.binding.policy,
            "depth": 1,
            "duration_ms": duration_ms,
            "usage": usage,
        }
        return event.model_copy(update={"payload": payload})

    def _record_terminal_span(
        self, event: RuntimeEvent, state: _TaskState, *, run_id: str
    ) -> None:
        if self._observability is None:
            return
        payload = event.payload
        usage = self._safe_usage(payload)
        attributes: dict[str, str | bool | int | float] = {
            "run.id": run_id,
            "langfuse.observation.type": "agent",
            "langfuse.observation.metadata.alias": state.binding.alias,
            "langfuse.observation.metadata.agent_name": state.binding.agent_name,
            "langfuse.version": state.binding.agent_version,
            "harness.subagent.alias": state.binding.alias,
            "harness.subagent.agent_name": state.binding.agent_name,
            "harness.subagent.agent_version": state.binding.agent_version,
            "harness.subagent.policy": state.binding.policy,
            "harness.subagent.depth": 1,
            "harness.subagent.status": str(payload.get("status", "unknown")),
            "harness.subagent.duration_ms": int(payload.get("duration_ms", 0)),
            "langfuse.observation.level": (
                "ERROR" if event.type == "subagent.failed" else "DEFAULT"
            ),
            "langfuse.observation.status_message": str(
                payload.get("status", "unknown")
            ),
        }
        for key in ("total_tokens", "tool_uses"):
            if key in usage:
                metric = "total_units" if key == "total_tokens" else key
                attributes[f"harness.subagent.usage.{metric}"] = usage[key]
        with self._observability.span("harness.subagent.run", attributes=attributes):
            if event.type == "subagent.failed":
                self._observability.mark_current_span_error(
                    str(payload.get("status", "failed"))
                )

    def process(self, event: RuntimeEvent, *, run_id: str) -> list[RuntimeEvent]:
        self._alias_from_tool_request(event)
        if event.type in _RUNTIME_TASK_TYPES:
            promoted = self._promote_runtime_task(event)
            if promoted is None:
                return [event]
            event = promoted
        if not event.type.startswith("subagent.") or event.type == "subagent.delta":
            return [event]
        task_id = str(event.payload.get("task_id", ""))
        if not task_id:
            raise SubagentGovernanceError("subagent lifecycle event is missing task_id")
        now = time.monotonic()
        state = self._tasks.get(task_id)
        if event.type == "subagent.started":
            if task_id in self._started_task_ids:
                raise SubagentGovernanceError(f"duplicate subagent start: {task_id}")
            if len(self._started_task_ids) >= self._limits.max_subagent_tasks:
                raise SubagentGovernanceError(
                    "subagent task count exceeds "
                    f"maxSubagentTasks={self._limits.max_subagent_tasks}"
                )
            binding = self._bindings[self._resolve_alias(event)]
            state = _TaskState(
                task_id=task_id,
                binding=binding,
                started_at=now,
                parent_tool_use_id=(
                    str(event.payload["parent_tool_use_id"])
                    if event.payload.get("parent_tool_use_id")
                    else None
                ),
            )
            self._tasks[task_id] = state
            self._started_task_ids.add(task_id)
            if len(self._tasks) > self._limits.max_concurrent_subagents:
                self._tasks.pop(task_id, None)
                raise SubagentGovernanceError(
                    "concurrent subagents exceed "
                    f"maxConcurrentSubagents={self._limits.max_concurrent_subagents}"
                )
        elif state is None:
            if len(self._started_task_ids) >= self._limits.max_subagent_tasks:
                raise SubagentGovernanceError(
                    "subagent task count exceeds "
                    f"maxSubagentTasks={self._limits.max_subagent_tasks}"
                )
            if len(self._tasks) >= self._limits.max_concurrent_subagents:
                raise SubagentGovernanceError(
                    "concurrent subagents exceed "
                    f"maxConcurrentSubagents={self._limits.max_concurrent_subagents}"
                )
            binding = self._bindings[self._resolve_alias(event)]
            state = _TaskState(
                task_id=task_id,
                binding=binding,
                started_at=now,
                parent_tool_use_id=None,
            )
            self._tasks[task_id] = state
            self._started_task_ids.add(task_id)
        enriched = self._enriched(event, state, now=now)
        if event.type in _TERMINAL_TYPES:
            self._tasks.pop(task_id, None)
            self._record_terminal_span(enriched, state, run_id=run_id)
        return [enriched]

    def fail_unfinished(self, *, reason: str, run_id: str) -> list[RuntimeEvent]:
        events: list[RuntimeEvent] = []
        now = time.monotonic()
        for task_id, state in tuple(self._tasks.items()):
            event = RuntimeEvent(
                type="subagent.failed",
                payload={
                    "task_id": task_id,
                    "status": "failed",
                    "error_code": reason,
                    "parent_tool_use_id": state.parent_tool_use_id,
                },
            )
            enriched = self._enriched(event, state, now=now)
            self._record_terminal_span(enriched, state, run_id=run_id)
            events.append(enriched)
        self._tasks.clear()
        return events
