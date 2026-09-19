"""DeepAgents middleware that decides before a tool executes.

DeepAgents' counterpart of the Claude SDK ``PreToolUse`` hook is
``AgentMiddleware.awrap_tool_call``: it observes every tool call after the model
produced its arguments and before the tool runs, and it may return a
``ToolMessage`` instead of calling the handler. That single interception point
carries the whole platform authorization sequence.

Two platform invariants shape this gate:

* **DeepAgents' own ``interrupt_on`` is never enabled.** It would add a second
  suspension mechanism (LangGraph ``__interrupt__``) competing with the
  platform's ``RunStatus.WAITING_APPROVAL``. Approvals go through
  ``ApprovalService(inline=True)`` exactly like the Claude runtime, so the
  Harness remains the only component that flips Run state.
* **The gate owns the ``tool.request`` fact.** It writes the request, the
  decision and the denial straight into the durable Run event stream, and the
  runtime drops ``tool.request`` from the events it yields. The worker's
  generic policy pass therefore neither re-evaluates the call nor suspends the
  Run a second time, which keeps exactly one approval mechanism per Run. This
  mirrors ``ClaudeSdkRuntime``.

Tool names are translated back into the platform vocabulary before any policy
runs: DeepAgents exposes ``execute``/``write_file``/..., while every policy
rule, quota resource and containment check is written against
``Bash``/``Write``/... See ``deepagents_plan.FILESYSTEM_TOOL_TO_BUILTIN``.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, cast
from uuid import uuid4

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from harness.application.approvals import ApprovalService
from harness.application.events import EventService
from harness.context.service import ContextService
from harness.core.models import ApprovalStatus
from harness.observability.provider import Observability
from harness.policy.bash_safety import sandboxed_bash_is_low_risk
from harness.policy.models import (
    ContextTrust,
    PolicyContext,
    PolicyDecision,
    PolicyResult,
)
from harness.policy.profiles import PolicyProfileRegistry
from harness.policy.results import stricter_trust
from harness.policy.rules import PolicyEngine
from harness.quota.models import QuotaResource
from harness.quota.repositories import QuotaExceededError
from harness.quota.service import QuotaService
from harness.runtime.approval_review import approval_argument_summary, approval_risk
from harness.runtime.audit_redaction import redact_text, redact_tool_arguments
from harness.runtime.base import RuntimeContext
from harness.runtime.deepagents_plan import FILESYSTEM_TOOL_TO_BUILTIN
from harness.runtime.file_capabilities import RunFileCapabilities
from harness.runtime.input_redaction import (
    INTERNAL_AGENT_ASSET_MARKER,
    STAGED_INPUT_READ_MARKER,
    internal_agent_asset_access,
    staged_input_paths,
    staged_read_path,
)
from harness.runtime.sandbox_tools import canonical_tool_name

_DENIED_PREFIX = "Tool call denied by platform policy"


def canonical_deepagents_tool(name: str) -> str:
    """Translate a DeepAgents tool name into the platform policy vocabulary."""

    return FILESYSTEM_TOOL_TO_BUILTIN.get(name, canonical_tool_name(name))


class DeepagentsToolGate(AgentMiddleware):
    """Authorize every DeepAgents tool call against platform policy."""

    def __init__(
        self,
        *,
        context: RuntimeContext,
        approvals: ApprovalService,
        events: EventService,
        policy: PolicyEngine | None = None,
        profiles: PolicyProfileRegistry | None = None,
        quotas: QuotaService | None = None,
        context_service: ContextService | None = None,
        observability: Observability | None = None,
        declared_tools: frozenset[str] = frozenset(),
    ) -> None:
        if (policy is None) == (profiles is None):
            raise ValueError("configure exactly one policy engine or profile registry")
        self._context = context
        self._approvals = approvals
        self._events = events
        self._quotas = quotas
        self._context_service = context_service
        self._observability = observability
        self._declared_tools = declared_tools
        self._file_capabilities = RunFileCapabilities(context)
        resolved = context.resolved_policy
        self._policy_id = resolved.policy_id if resolved is not None else "local-standard"
        if resolved is not None:
            self._policy = resolved.call_policy
        elif profiles is not None:
            self._policy = profiles.resolve(self._policy_id)
        else:
            assert policy is not None
            self._policy = policy
        self._context_trust = ContextTrust.SAFE
        self._trust_loaded = False

    @property
    def name(self) -> str:
        return "harness-tool-gate"

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[Any]]],
    ) -> ToolMessage | Command[Any]:
        context = self._context
        tool_call = request.tool_call
        raw_name = str(tool_call.get("name") or "")
        tool_name = canonical_deepagents_tool(raw_name)
        tool_call_id = str(tool_call.get("id") or "") or f"call-{uuid4().hex}"
        # `ToolCall.args` is a required mapping in the middleware contract, so read
        # it directly rather than re-validating a shape the caller already promised.
        arguments: dict[str, Any] = dict(tool_call.get("args") or {})
        context_trust = await self._context_trust_high_watermark()
        started_at_ns = time.time_ns()

        request_payload: dict[str, Any] = {
            "name": tool_name,
            "tool_call_id": tool_call_id,
            "arguments": arguments,
            "policy_checked": True,
            "policy_profile": self._policy_id,
            "context_trust": context_trust.value,
            "message_id": context.assistant_message_id,
            "sandbox": {
                "provider": context.sandbox_provider,
                "isolation": context.sandbox_isolation.value,
            },
        }
        if tool_name != raw_name:
            request_payload["runtime_tool_name"] = raw_name
        relative_input_path = staged_read_path(
            request_payload,
            workspace=context.workspace,
            staged_paths=staged_input_paths(context.workspace, context.input_files),
        )
        if relative_input_path is not None:
            arguments = {**arguments, "file_path": relative_input_path}
            request_payload["arguments"] = arguments
            request_payload[STAGED_INPUT_READ_MARKER] = True
            request = request.override(tool_call={**tool_call, "args": arguments})
        if internal_agent_asset_access(request_payload):
            request_payload[INTERNAL_AGENT_ASSET_MARKER] = True
        audit_arguments = request_payload.get("arguments")
        if isinstance(audit_arguments, dict):
            request_payload["arguments"] = redact_tool_arguments(
                tool_name, cast(dict[str, Any], audit_arguments)
            )
        await self._append("tool.request", request_payload)

        write_target = None
        if tool_name in {"Write", "Edit"}:
            write_target = self._file_capabilities.target(arguments)
            if write_target is None:
                reason = "write path must stay within the run workspace"
                await self._deny(tool_call_id, reason)
                return self._denied(tool_call_id, raw_name, reason)
            self._file_capabilities.observe(write_target)

        if write_target is not None and self._file_capabilities.is_generated(write_target):
            result = PolicyResult(
                decision=PolicyDecision.ALLOW,
                rule_name="run-generated-file",
                reason="matched run-created file capability",
            )
        else:
            result = self._policy.evaluate(
                PolicyContext(
                    tenant_id=context.run.tenant_id,
                    agent_name=context.session.agent_name,
                    tool_name=tool_name,
                    arguments=arguments,
                    sandbox_isolation=context.sandbox_isolation,
                    context_trust=context_trust,
                )
            )
        result = self._apply_overrides(
            result,
            raw_name=raw_name,
            tool_name=tool_name,
            arguments=arguments,
        )
        if result.decision is PolicyDecision.DENY:
            await self._deny(tool_call_id, result.reason)
            return self._denied(tool_call_id, raw_name, result.reason)

        if result.decision is PolicyDecision.ASK:
            decision = await self._request_approval(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                arguments=arguments,
                result=result,
            )
            if decision is not ApprovalStatus.APPROVED:
                reason = "tool use was not approved"
                await self._deny(tool_call_id, reason)
                return self._denied(tool_call_id, raw_name, reason)

        if tool_name == "Write" and write_target is not None:
            self._file_capabilities.note_authorized_write(tool_call_id, write_target)

        quota_reason = await self._consume_quota(raw_name, tool_name, tool_call_id)
        if quota_reason is not None:
            await self._deny(tool_call_id, quota_reason)
            return self._denied(tool_call_id, raw_name, quota_reason)

        await self._append(
            "tool.allowed",
            {"tool_call_id": tool_call_id, "permission_stage": "harness-final"},
        )
        output = await handler(request)
        await self._after_tool(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments=arguments,
            started_at_ns=started_at_ns,
            failed=isinstance(output, ToolMessage) and output.status == "error",
        )
        return output

    def _apply_overrides(
        self,
        result: PolicyResult,
        *,
        raw_name: str,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> PolicyResult:
        """Apply the platform's three narrow allow overrides, in order.

        Each one only fires on an ``implicit-deny`` or a sandboxed ``Bash``
        review, so an operator's explicit rule always wins.
        """

        context = self._context
        if (
            tool_name == "Bash"
            and result.decision is PolicyDecision.ASK
            and sandboxed_bash_is_low_risk(
                str(arguments.get("command", "")),
                workspace=str(context.workspace),
                remote_workspace=context.remote_workspace,
                generated_python_files=self._file_capabilities.generated_python_files(),
            )
        ):
            return PolicyResult(
                decision=PolicyDecision.ALLOW,
                rule_name="sandbox-low-risk-bash",
                reason="matched sandbox low-risk Bash policy",
            )
        if (
            result.decision is PolicyDecision.DENY
            and result.rule_name == "implicit-deny"
            and raw_name.startswith("mcp__")
            and not raw_name.startswith("mcp__harness-python-")
            and (raw_name in self._declared_tools or tool_name in self._declared_tools)
        ):
            return PolicyResult(
                decision=PolicyDecision.ALLOW,
                rule_name="published-mcp-tool",
                reason="matched MCP tool declared by the published Agent tool directory",
            )
        if (
            result.decision is PolicyDecision.DENY
            and result.rule_name == "implicit-deny"
            and raw_name.startswith("mcp__harness-python-")
            and raw_name in self._declared_tools
            and context.sandbox_command_executor is not None
        ):
            return PolicyResult(
                decision=PolicyDecision.ALLOW,
                rule_name="declared-sandbox-python-tool",
                reason="matched declared Bundle Python tool in isolated Sandbox",
            )
        return result

    async def _request_approval(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        result: PolicyResult,
    ) -> ApprovalStatus:
        """Pause inside the live graph until a human decides.

        ``inline=True`` keeps the worker task polling, so the Run stays
        ``WAITING_APPROVAL`` while the LangGraph state is still in memory and
        resumes on the same thread instead of replaying the whole Run.
        """

        context = self._context
        approval = await self._approvals.request(
            tenant_id=context.run.tenant_id,
            run_id=context.run.run_id,
            tool_call_id=tool_call_id,
            reason=result.reason,
            message_id=context.assistant_message_id,
            inline=True,
            tool_name=tool_name,
            argument_summary=approval_argument_summary(arguments),
            sandbox_provider=context.sandbox_provider,
            sandbox_isolation=context.sandbox_isolation.value,
            policy_rule=result.rule_name,
            risk=approval_risk(tool_name),
        )
        try:
            return await self._approvals.wait_for_decision(approval.approval_id)
        except asyncio.CancelledError:
            await asyncio.shield(
                self._approvals.cancel_pending(
                    tenant_id=context.run.tenant_id,
                    approval_id=approval.approval_id,
                    reason="tool authorization wait interrupted",
                )
            )
            raise

    async def _consume_quota(
        self,
        raw_name: str,
        tool_name: str,
        tool_call_id: str,
    ) -> str | None:
        """Charge the Run ledger; return a denial reason when the quota is spent."""

        if self._quotas is None:
            return None
        context = self._context
        try:
            if raw_name.startswith("mcp__"):
                await self._quotas.consume(
                    tenant_id=context.run.tenant_id,
                    resource=QuotaResource.MCP_REQUESTS,
                    amount=1,
                    subject_id=context.run.run_id,
                    idempotency_key=f"run:{context.run.run_id}:mcp:{tool_call_id}",
                    agent_name=context.session.agent_name,
                    environment=context.session.environment,
                )
            elif tool_name in {"Task", "Agent"}:
                await self._quotas.reserve(
                    tenant_id=context.run.tenant_id,
                    resource=QuotaResource.CONCURRENT_SUBAGENTS,
                    amount=1,
                    subject_id=context.run.run_id,
                    idempotency_key=f"run:{context.run.run_id}:subagent:{tool_call_id}",
                    agent_name=context.session.agent_name,
                    environment=context.session.environment,
                    ttl_seconds=3600,
                )
        except QuotaExceededError as error:
            return f"quota exceeded for {error.resource.value}"
        return None

    async def _after_tool(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        started_at_ns: int,
        failed: bool,
    ) -> None:
        """Settle file capabilities, context trust and the observability span."""

        if failed:
            self._file_capabilities.note_write_failed(tool_call_id)
        else:
            self._file_capabilities.note_write_succeeded(tool_call_id, tool_name)
            await self._promote_result_trust(tool_call_id, tool_name)
        self._record_span(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments=arguments,
            started_at_ns=started_at_ns,
            failed=failed,
        )

    async def _promote_result_trust(self, tool_call_id: str, tool_name: str) -> None:
        """Tighten the Run's trust when a tool returned untrusted content.

        The Claude runtime gets this from the SDK's ``PostToolUse`` hook; here
        the only evidence that a tool succeeded is the handler returning, so the
        promotion is evaluated at the same point in the call.
        """

        context = self._context
        resolved = context.resolved_policy
        if resolved is None:
            return
        result_policy = resolved.result_policy.evaluate(
            tool_name, agent_name=context.session.agent_name
        )
        current = await self._context_trust_high_watermark()
        next_trust = stricter_trust(current, result_policy.trust)
        if next_trust is current:
            return
        if self._context_service is None:
            self._context_trust = next_trust
        else:
            state = await self._context_service.promote_trust(
                context.run.tenant_id,
                context.session.user_id,
                context.run.session_id,
                next_trust,
            )
            self._context_trust = state.trust_high_watermark
        await self._append(
            "context.trust.changed",
            {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "previous": current.value,
                "current": self._context_trust.value,
                "policy_rule": result_policy.rule_name,
            },
        )

    async def _context_trust_high_watermark(self) -> ContextTrust:
        if not self._trust_loaded:
            self._trust_loaded = True
            if self._context_service is not None:
                context = self._context
                state = await self._context_service.state(
                    context.run.tenant_id,
                    context.session.user_id,
                    context.run.session_id,
                )
                self._context_trust = state.trust_high_watermark
        return self._context_trust

    def _record_span(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        started_at_ns: int,
        failed: bool,
    ) -> None:
        if self._observability is None:
            return
        context = self._context
        ended_at_ns = time.time_ns()
        status = "failed" if failed else "succeeded"
        self._observability.record_completed_span(
            tool_name,
            started_at_ns=started_at_ns,
            ended_at_ns=ended_at_ns,
            attributes={
                "run.id": context.run.run_id,
                "harness.tool.name": tool_name,
                "harness.tool.call_id": tool_call_id,
                "harness.tool.status": status,
                "harness.tool.duration_ms": max(
                    0, round((ended_at_ns - started_at_ns) / 1_000_000)
                ),
                "harness.policy.profile": self._policy_id,
                "harness.sandbox.provider": context.sandbox_provider,
                "harness.sandbox.isolation": context.sandbox_isolation.value,
                "harness.sandbox.enforcement": context.sandbox_enforcement.value,
                "harness.runtime": "deepagents",
                "langfuse.observation.type": "tool",
                "langfuse.observation.level": "ERROR" if failed else "DEFAULT",
                "langfuse.observation.status_message": status,
                "langfuse.observation.metadata.call_id": tool_call_id,
                "langfuse.observation.metadata.policy": self._policy_id,
                "langfuse.observation.metadata.sandbox": (
                    f"{context.sandbox_provider}:{context.sandbox_isolation.value}"
                ),
            },
            input_value=arguments,
            output_value={"status": status},
            error_type="tool_failed" if failed else None,
        )

    async def _append(self, event_type: str, payload: dict[str, Any]) -> None:
        context = self._context
        await self._events.append(
            tenant_id=context.run.tenant_id,
            run_id=context.run.run_id,
            session_id=context.run.session_id,
            event_type=event_type,
            payload=payload,
        )

    async def _deny(self, tool_call_id: str, reason: str) -> None:
        await self._append(
            "tool.result",
            {
                "tool_call_id": tool_call_id,
                "is_error": True,
                "error": {"code": "policy_denied", "message": reason},
            },
        )

    @staticmethod
    def _denied(tool_call_id: str, tool_name: str, reason: str) -> ToolMessage:
        """Hand the model a denial it can reason about instead of a crash."""

        return ToolMessage(
            content=f"{_DENIED_PREFIX}: {redact_text(reason)}",
            tool_call_id=tool_call_id,
            name=tool_name,
            status="error",
        )
