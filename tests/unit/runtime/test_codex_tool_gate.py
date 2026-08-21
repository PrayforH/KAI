from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from harness.application.approvals import ApprovalService
from harness.application.events import EventService
from harness.core.models import ApprovalRequest, ApprovalStatus, Run, RunStatus, Session
from harness.policy.models import PolicyDecision, PolicyRule
from harness.policy.results import ResultPolicyEngine
from harness.policy.rules import PolicyEngine
from harness.policy.runtime import ResolvedPolicy
from harness.runtime.base import RuntimeContext
from harness.runtime.codex_protocol import CodexMessage, CodexMessageKind
from harness.runtime.codex_tool_gate import CodexToolGate

NOW = datetime(2026, 8, 22, tzinfo=UTC)


class FakeApprovals:
    def __init__(self, decision: ApprovalStatus = ApprovalStatus.APPROVED) -> None:
        self.decision = decision
        self.requested: list[dict[str, Any]] = []

    async def request(self, **kwargs: Any) -> ApprovalRequest:
        self.requested.append(kwargs)
        return ApprovalRequest(
            approval_id="approval-1",
            run_id=str(kwargs["run_id"]),
            tenant_id=str(kwargs["tenant_id"]),
            tool_call_id=str(kwargs["tool_call_id"]),
            status=ApprovalStatus.PENDING,
            reason=str(kwargs["reason"]),
            expires_at=NOW + timedelta(minutes=15),
            created_at=NOW,
            inline=True,
        )

    async def wait_for_decision(self, approval_id: str) -> ApprovalStatus:
        assert approval_id == "approval-1"
        return self.decision


class FakeEvents:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    async def append(self, **kwargs: Any) -> None:
        self.items.append(kwargs)


def _context(tmp_path: Path, decision: PolicyDecision) -> RuntimeContext:
    session = Session(
        session_id="session-1",
        tenant_id="tenant-a",
        user_id="user-a",
        agent_name="agent-a",
        agent_version="1.0.0",
        runtime_type="codex-app-server",
        created_at=NOW,
    )
    run = Run(
        run_id="run-1",
        session_id=session.session_id,
        tenant_id=session.tenant_id,
        status=RunStatus.RUNNING,
        idempotency_key="idem-1",
        created_at=NOW,
        updated_at=NOW,
    )
    policy = PolicyEngine(
        [PolicyRule(name="bash-policy", tool="Bash", decision=decision)]
    )
    return RuntimeContext(
        run=run,
        session=session,
        workspace=tmp_path,
        resolved_policy=ResolvedPolicy(
            policy_id="test",
            revision=1,
            content_hash="a" * 64,
            call_policy=policy,
            result_policy=ResultPolicyEngine([]),
        ),
    )


def _request(command: str = "git status") -> CodexMessage:
    return CodexMessage(
        CodexMessageKind.SERVER_REQUEST,
        {
            "id": 7,
            "method": "item/commandExecution/requestApproval",
            "params": {
                "itemId": "item-1",
                "command": command,
                "cwd": "/workspace",
            },
        },
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("policy_decision", "expected_codex_decision", "event_type"),
    [
        (PolicyDecision.ALLOW, "accept", "tool.allowed"),
        (PolicyDecision.DENY, "decline", "tool.denied"),
    ],
)
async def test_maps_policy_decision_to_codex_response(
    tmp_path: Path,
    policy_decision: PolicyDecision,
    expected_codex_decision: str,
    event_type: str,
) -> None:
    approvals = FakeApprovals()
    events = FakeEvents()
    gate = CodexToolGate(
        approvals=cast(ApprovalService, approvals),
        events=cast(EventService, events),
    )

    response = await gate.authorize(_context(tmp_path, policy_decision), _request())

    assert response == {"decision": expected_codex_decision}
    assert events.items[-1]["event_type"] == event_type
    assert approvals.requested == []


@pytest.mark.asyncio
async def test_ask_waits_on_inline_platform_approval(tmp_path: Path) -> None:
    approvals = FakeApprovals(ApprovalStatus.APPROVED)
    events = FakeEvents()
    gate = CodexToolGate(
        approvals=cast(ApprovalService, approvals),
        events=cast(EventService, events),
    )

    response = await gate.authorize(
        _context(tmp_path, PolicyDecision.ASK),
        _request("deploy --token private-value"),
    )

    assert response == {"decision": "accept"}
    assert approvals.requested[0]["inline"] is True
    assert approvals.requested[0]["tool_name"] == "Bash"
    assert "private-value" not in repr(approvals.requested[0]["argument_summary"])
    assert events.items[-1]["event_type"] == "tool.allowed"
