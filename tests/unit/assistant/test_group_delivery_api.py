"""HTTP aliases, membership denials and bounded query validation."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from harness.api.app import create_memory_app
from harness.assistant import api
from harness.assistant.group_delivery import GroupArtifactPage
from harness.core.errors import PermissionDeniedError


@pytest.mark.asyncio
async def test_artifact_http_bounds_and_read_contract():
    app = create_memory_app()
    service = SimpleNamespace(
        list_assistant_group_artifacts=AsyncMock(return_value=GroupArtifactPage(items=()))
    )
    app.dependency_overrides[api.reader] = lambda: SimpleNamespace(tenant_id="t", user_id="u")
    app.dependency_overrides[api.service] = lambda: service
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        path = "/v1/studio/assistant/groups/group/artifacts"
        for query in (
            "?limit=101",
            "?limit=0",
            "?offset=-1",
            "?offset=9223372036854775808",
            "?since=bad",
        ):
            result = await client.get(path + query)
            assert result.status_code == 422, result.text
        service.list_assistant_group_artifacts.assert_not_awaited()
        result = await client.get(path + "?limit=2&offset=4&since=2026-11-01T00:00:00Z")
        assert result.status_code == 200, result.text
        assert result.json() == {"items": [], "nextOffset": None}
        kwargs = service.list_assistant_group_artifacts.await_args.kwargs
        assert kwargs["limit"] == 2 and kwargs["offset"] == 4
        assert kwargs["since"].tzinfo is not None
        service.list_assistant_group_artifacts.side_effect = PermissionDeniedError(
            "not a group member"
        )
        denied = await client.get(path)
        assert denied.status_code == 403, denied.text


@pytest.mark.asyncio
async def test_delivery_http_read_has_no_dispatch_and_nonmember_403():
    app = create_memory_app()
    service = SimpleNamespace(
        get_assistant_group_delivery=AsyncMock(side_effect=PermissionDeniedError("not a member"))
    )
    app.dependency_overrides[api.reader] = lambda: SimpleNamespace(
        tenant_id="t", user_id="outsider"
    )
    app.dependency_overrides[api.service] = lambda: service
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        result = await client.get("/v1/studio/assistant/groups/group/messages/message/deliveries")
        assert result.status_code == 403, result.text
        service.get_assistant_group_delivery.assert_awaited_once_with(
            "t", "outsider", "group", "message"
        )
