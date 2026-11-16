"""Removing one message is unavailable over HTTP; dissolving a group stays usable."""

from dataclasses import replace

import pytest
from httpx import ASGITransport, AsyncClient

from harness.api.app import create_memory_app
from tests.integration.api.test_agui_history_pagination import HEADERS
from tests.unit.assistant.test_group_manage_library import _service


@pytest.mark.asyncio
async def test_message_delete_route_is_absent_while_group_dissolve_route_remains():
    app = create_memory_app()
    service, repo, _ = _service()
    repo.seed_group(user_id="user-1")
    app.state.container = replace(app.state.container, assistant=service)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.delete(
            "/v1/studio/assistant/groups/group_a/messages/gmsg_1",
            headers=HEADERS,
        )
        assert response.status_code in (404, 405), response.text
        assert "group_a" in repo.groups
        response = await client.delete("/v1/studio/assistant/groups/group_a", headers=HEADERS)
        assert response.status_code == 204, response.text
        assert "group_a" not in repo.groups
