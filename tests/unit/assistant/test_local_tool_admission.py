from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from harness.assistant.models import Specialist
from harness.assistant.permissions import pending_local_tools, require_local_tools
from harness.assistant.repository import PostgresAssistantRepository
from harness.core.errors import ConflictError
from tests.unit.assistant.test_service import NOW, setup


def test_legacy_configuration_and_explicit_grants():
    require_local_tools({})
    config = {
        "required_local_tools": ["run_commands", "run_commands", "access_private_files"],
        "local_tool_permissions": ["run_commands"],
    }
    assert pending_local_tools(config) == ["access_private_files"]
    with pytest.raises(ConflictError, match="access_private_files"):
        require_local_tools(config)
    require_local_tools(config | {"local_tool_permissions": config["required_local_tools"]})


@pytest.mark.parametrize("value", ["run_commands", ["unknown"], [None], None])
def test_malformed_declarations_cannot_silently_authorize(value):
    with pytest.raises(ConflictError):
        require_local_tools({"required_local_tools": value})


@pytest.mark.asyncio
async def test_revocation_while_queued_prevents_model_submission():
    service, execution, repo, _, agui, runs, _ = setup()
    repo.get.return_value = repo.get.return_value.model_copy(update={"specialist_id": "bot"})
    repo.get_specialist.return_value = SimpleNamespace(
        config={"required_local_tools": ["run_commands"]}
    )
    runs.find_by_idempotency_key.return_value = None
    result = await service._reconcile(execution)
    assert result.status.value == "failed"
    assert result.error_code == "LocalToolPermissionRequired"
    assert result.run_id is None
    agui.create_run_with_result.assert_not_awaited()
    repo.acquire_desktop_turn.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_of_existing_run_does_not_duplicate_or_cancel_it():
    service, execution, repo, _, agui, runs, _ = setup()
    repo.get.return_value = repo.get.return_value.model_copy(update={"specialist_id": "bot"})
    result = await service._reconcile(execution)
    assert result.run_id == "run"
    agui.create_run_with_result.assert_not_awaited()
    runs.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_permission_config_rejected_before_persisting():
    service, _, repo, _, _, _, _ = setup()
    with pytest.raises(ConflictError):
        await service.configure_specialist(
            "t", "u", "bot", config={"local_tool_permissions": ["unknown"]}, expected_revision=1
        )
    repo.update_specialist.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_bot", [False, True])
async def test_enqueue_gate_rejects_before_writing_intent(missing_bot):
    _, _, repo, _, _, _, _ = setup()
    objective = repo.get.return_value.model_copy(update={"specialist_id": "bot"})
    specialist = Specialist(
        specialistId="bot",
        tenantId="t",
        userId="u",
        slug="bot",
        name="Bot",
        description="test",
        agentName="assistant",
        agentVersion="1",
        config={"required_local_tools": ["run_commands"]},
        createdAt=NOW,
        updatedAt=NOW,
    )
    session = AsyncMock()
    session.add = Mock()
    session.scalar.side_effect = [
        None,
        None if missing_bot else SimpleNamespace(payload=specialist.model_dump(by_alias=True)),
    ]
    storage = PostgresAssistantRepository(AsyncMock())
    row = SimpleNamespace(
        objective_id=objective.objective_id, payload=objective.model_dump(by_alias=True)
    )
    with pytest.raises(ConflictError, match="exists|待授权"):
        await storage._enqueue(session, row, trigger_key="event", now=NOW)
    session.add.assert_not_called()
