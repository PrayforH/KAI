from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from harness.assistant.api import list_specialists
from harness.assistant.service import AssistantService
from harness.core.errors import NotFoundError
from harness.core.models import RunStatus
from tests.unit.assistant.test_mention_message import _specialist


@pytest.mark.asyncio
async def test_directory_projects_owned_legacy_and_current_runs_without_task_pagination():
    bots = [_specialist().model_copy(update={"specialist_id": f"bot-{i}"}) for i in range(3)]
    repo = AsyncMock()
    repo.list_specialists.return_value = bots
    repo.list.return_value = []
    agui = AsyncMock()
    agui.get_thread_record.side_effect = [
        SimpleNamespace(session_ids=("s1",)),
        SimpleNamespace(session_ids=("s2",)),
        NotFoundError("new bot"),
    ]
    runs = AsyncMock()
    now = datetime.now(UTC)
    runs.list_for_sessions.return_value = [
        SimpleNamespace(run_id="r1", session_id="s1", updated_at=now, status=RunStatus.RUNNING),
        SimpleNamespace(run_id="r2", session_id="s2", updated_at=now, status=RunStatus.SUCCEEDED),
    ]
    service = AssistantService(
        repo, agents=None, auth=None, projects=None, agui=agui, runs=runs, events=None
    )
    service.specialist_thread = AsyncMock(side_effect=["main", "assistant_objective_legacy", "new"])
    items = await list_specialists(SimpleNamespace(tenant_id="tenant", user_id="owner"), service)
    assert [(i.activity.thread_id, i.activity.status) for i in items] == [
        ("main", "running"),
        ("assistant_objective_legacy", "succeeded"),
        ("new", "idle"),
    ]
    assert (
        items[1].model_dump(by_alias=True)["activity"]["threadId"] == "assistant_objective_legacy"
    )
    assert agui.get_thread_record.await_args_list[1].kwargs == {
        "tenant_id": "tenant",
        "user_id": "owner",
        "thread_id": "assistant_objective_legacy",
    }
    runs.list_for_sessions.assert_awaited_once()
    assert set(runs.list_for_sessions.await_args.args[1]) == {"s1", "s2"}
    assert [call[0] for call in repo.mock_calls] == ["list_specialists", "list"]
