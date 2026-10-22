"""Exercise orchestration boundaries, including submission crash recovery."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from harness.assistant.models import Objective, ObjectiveBrief, ObjectiveExecution, ObjectiveStatus
from harness.assistant.service import AssistantService
from harness.core.errors import NotFoundError, PermissionDeniedError
from harness.core.models import Run, RunStatus

NOW = datetime(2026, 10, 22, tzinfo=UTC)


def setup(status=ObjectiveStatus.ACTIVE):
    brief = ObjectiveBrief(
        title="跟进", instructions="检查材料", agentName="assistant", agentVersion="1"
    )
    objective = Objective(
        **brief.model_dump(),
        objectiveId="o",
        tenantId="t",
        userId="u",
        status=status,
        createdAt=NOW,
        updatedAt=NOW,
    )
    execution = ObjectiveExecution(
        executionId="e",
        objectiveId="o",
        tenantId="t",
        userId="u",
        triggerKey="manual:1",
        objectiveRevision=1,
        brief=brief,
        threadId="thread",
        createdAt=NOW,
    )
    repo, agents, auth, projects, agui, runs, events = [AsyncMock() for _ in range(7)]
    repo.get.return_value = objective
    auth.profile.return_value = (SimpleNamespace(disabled=False), SimpleNamespace(role="member"))
    agui.get_binding.return_value = SimpleNamespace(session_id="session")
    run = Run(
        run_id="run",
        session_id="session",
        tenant_id="t",
        status=RunStatus.RUNNING,
        idempotency_key="e",
        created_at=NOW,
        updated_at=NOW,
    )
    runs.find_by_idempotency_key.return_value = run
    events.list_after.return_value = []
    service = AssistantService(
        repo, agents=agents, auth=auth, projects=projects, agui=agui, runs=runs, events=events
    )
    return service, execution, repo, auth, agui, runs, run


@pytest.mark.asyncio
async def test_recover_run_submitted_before_process_crash_without_resubmission():
    service, execution, _, _, agui, runs, _ = setup()
    result = await service._reconcile(execution)
    assert result.run_id == "run"
    agui.create_run_with_result.assert_not_called()
    runs.find_by_idempotency_key.assert_awaited_once_with("t", "session", "e")


@pytest.mark.asyncio
async def test_pause_preserves_running_task_but_stop_requests_actual_cancellation():
    service, execution, _, _, agui, runs, run = setup(ObjectiveStatus.PAUSED)
    result = await service._reconcile(execution)
    assert result.status.value == "running"
    runs.cancel.assert_not_called()
    agui.create_run_with_result.assert_not_called()
    service, execution, _, _, agui, runs, run = setup(ObjectiveStatus.CANCELLED)
    runs.cancel.return_value = run.model_copy(update={"status": RunStatus.CANCELLING})
    result = await service._reconcile(execution)
    assert (
        result.status.value == "running"
    )  # Wait for real cancellation, not an optimistic success.
    runs.cancel.assert_awaited_once_with("t", "run")
    agui.create_run_with_result.assert_not_called()


@pytest.mark.asyncio
async def test_stopped_unsubmitted_execution_never_starts():
    service, execution, _, _, agui, runs, _ = setup(ObjectiveStatus.CANCELLED)
    agui.get_binding.side_effect = NotFoundError("no session")
    result = await service._reconcile(execution)
    assert result.status.value == "cancelled"
    agui.create_run_with_result.assert_not_called()
    runs.cancel.assert_not_called()


@pytest.mark.asyncio
async def test_disabled_owner_cannot_start_or_continue_an_objective():
    service, execution, _, auth, agui, runs, _ = setup()
    auth.profile.return_value = (SimpleNamespace(disabled=True), SimpleNamespace(role="member"))
    runs.find_by_idempotency_key.return_value = None
    with pytest.raises(PermissionDeniedError):
        await service._reconcile(execution)
    agui.create_run_with_result.assert_not_called()


@pytest.mark.asyncio
async def test_success_is_execution_completion_not_objective_completion():
    service, execution, repo, _, agui, runs, run = setup()
    runs.find_by_idempotency_key.return_value = run.model_copy(
        update={"status": RunStatus.SUCCEEDED}
    )
    result = await service._reconcile(execution)
    assert result.status.value == "succeeded" and result.finished_at is not None
    repo.update.assert_not_called()
    agui.create_run_with_result.assert_not_called()
