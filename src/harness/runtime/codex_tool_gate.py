"""Bridge Codex app-server approval requests to Harness policy and approvals."""

from __future__ import annotations

import asyncio
from typing import Any, cast

from harness.application.approvals import ApprovalService
from harness.application.events import EventService
from harness.core.models import ApprovalStatus
from harness.policy.models import PolicyContext, PolicyDecision
from harness.runtime.audit_redaction import redact_tool_arguments
from harness.runtime.base import RuntimeContext
from harness.runtime.codex_protocol import CodexMessage


class CodexToolGate:
    """Make each app-server escalation follow the durable Harness policy path."""

    def __init__(self, *, approvals: ApprovalService, events: EventService) -> None:
        self._approvals = approvals
        self._events = events

    async def authorize(self, context: RuntimeContext, message: CodexMessage) -> object:
        method = message.payload.get("method")
        params = message.payload.get("params")
        typed_params = cast(dict[str, Any], params) if isinstance(params, dict) else {}
        if method == "item/commandExecution/requestApproval":
            tool_name = "Bash"
            arguments: dict[str, Any] = {
                "command": str(typed_params.get("command") or ""),
                "cwd": str(typed_params.get("cwd") or ""),
            }
            risk = "high"
        elif method == "item/fileChange/requestApproval":
            tool_name = "Edit"
            arguments = {
                "grant_root": str(typed_params.get("grantRoot") or ""),
                "reason": str(typed_params.get("reason") or ""),
            }
            risk = "medium"
        else:
            return {"decision": "decline"}
        tool_call_id = str(
            typed_params.get("approvalId") or typed_params.get("itemId") or ""
        )
        if not tool_call_id:
            return {"decision": "decline"}
        policy = (
            context.resolved_policy.call_policy
            if context.resolved_policy is not None
            else None
        )
        if policy is None:
            await self._append_decision(
                context,
                tool_call_id=tool_call_id,
                event_type="tool.denied",
                reason="runtime policy is unavailable",
            )
            return {"decision": "decline"}
        result = policy.evaluate(
            PolicyContext(
                tenant_id=context.run.tenant_id,
                agent_name=context.session.agent_name,
                tool_name=tool_name,
                arguments=arguments,
            )
        )
        if result.decision is PolicyDecision.DENY:
            await self._append_decision(
                context,
                tool_call_id=tool_call_id,
                event_type="tool.denied",
                reason=result.reason,
                policy_rule=result.rule_name,
            )
            return {"decision": "decline"}
        if result.decision is PolicyDecision.ASK:
            approval = await self._approvals.request(
                tenant_id=context.run.tenant_id,
                run_id=context.run.run_id,
                tool_call_id=tool_call_id,
                reason=result.reason,
                message_id=context.assistant_message_id,
                inline=True,
                tool_name=tool_name,
                argument_summary=redact_tool_arguments(tool_name, arguments),
                sandbox_provider=context.sandbox_provider,
                sandbox_isolation=context.sandbox_isolation.value,
                policy_rule=result.rule_name,
                risk=risk,
            )
            try:
                decision = await self._approvals.wait_for_decision(approval.approval_id)
            except asyncio.CancelledError:
                await asyncio.shield(
                    self._approvals.cancel_pending(
                        tenant_id=context.run.tenant_id,
                        approval_id=approval.approval_id,
                        reason="Codex tool authorization wait interrupted",
                    )
                )
                raise
            if decision is not ApprovalStatus.APPROVED:
                return {"decision": "decline"}
        await self._append_decision(
            context,
            tool_call_id=tool_call_id,
            event_type="tool.allowed",
            reason=result.reason,
            policy_rule=result.rule_name,
        )
        return {"decision": "accept"}

    async def _append_decision(
        self,
        context: RuntimeContext,
        *,
        tool_call_id: str,
        event_type: str,
        reason: str,
        policy_rule: str | None = None,
    ) -> None:
        payload = {"tool_call_id": tool_call_id, "reason": reason}
        if policy_rule:
            payload["policy_rule"] = policy_rule
        await self._events.append(
            tenant_id=context.run.tenant_id,
            run_id=context.run.run_id,
            session_id=context.run.session_id,
            event_type=event_type,
            payload=payload,
        )
