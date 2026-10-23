"""Builder model choices affect calls and runs, never the authored default model."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from harness.studio.api import get_model_configuration_service
from tests.integration.api.test_agent_studio_api import (
    INITIAL_AGENT_RESPONSE,
    SERVICE_TOKEN,
    app_and_container,
    draft_request,
)

HEADERS = {
    "Authorization": f"Bearer {SERVICE_TOKEN}",
    "X-Tenant-ID": "builder-model",
    "X-User-ID": "owner",
}


@pytest.mark.asyncio
async def test_builder_generation_edit_and_repair_use_selected_model_without_changing_spec():
    application, _ = app_and_container(auto_execute=False)
    models = AsyncMock()
    application.dependency_overrides[get_model_configuration_service] = lambda: models
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        models.complete_text.return_value = INITIAL_AGENT_RESPONSE
        created = await client.post(
            "/v1/studio/drafts/from-task",
            headers=HEADERS,
            json={
                "task": "创建材料整理助手",
                "modelRouteOverride": "builder-other",
            },
        )
        assert created.status_code == 201, created.text
        assert models.complete_text.call_args.args[1] == "builder-other"
        draft = created.json()["draft"]
        path = f"/v1/studio/drafts/{draft['draftId']}"
        assert draft["spec"]["model"]["routeId"] != "builder-other"
        models.complete_text.side_effect = ["invalid", json.dumps({"reply": "说明", "changes": {}})]
        reply = await client.post(
            path + "/builder-conversation",
            headers=HEADERS,
            json={
                "expectedRevision": 1,
                "modelRouteOverride": "builder-second",
                "messages": [{"role": "user", "content": "说明一下职责"}],
            },
        )
        assert reply.status_code == 200, reply.text
        assert [call.args[1] for call in models.complete_text.call_args_list[-2:]] == [
            "builder-second",
            "builder-second",
        ]
        assert (await client.get(path, headers=HEADERS)).json() == draft
        count = models.complete_text.await_count
        denied = await client.post(
            path + "/builder-conversation",
            headers={**HEADERS, "X-User-ID": "other"},
            json={
                "expectedRevision": 1,
                "modelRouteOverride": "builder-second",
                "messages": [{"role": "user", "content": "说明"}],
            },
        )
        assert denied.status_code == 404
        assert models.complete_text.await_count == count


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime", ["claude-agent-sdk", "deepagents"])
async def test_preview_model_switches_on_followups_and_reset_does_not_inherit_previous(
    runtime: str,
):
    application, container = app_and_container(auto_execute=False)
    models = AsyncMock()
    models.resolve_runtime.return_value = SimpleNamespace(
        capabilities=frozenset({"tool_use", "streaming", "vision", "reasoning"})
    )
    application.dependency_overrides[get_model_configuration_service] = lambda: models
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        draft = (
            await client.post(
                "/v1/studio/drafts", headers=HEADERS, json=draft_request("independent-model")
            )
        ).json()
        path = f"/v1/studio/drafts/{draft['draftId']}"
        if draft["spec"]["runtime"] != runtime:
            saved = await client.put(
                path,
                headers=HEADERS,
                json={
                    "expectedRevision": draft["revision"],
                    "spec": {**draft["spec"], "runtime": runtime},
                },
            )
            assert saved.status_code == 200, saved.text
            draft = saved.json()

        body = {
            "expectedRevision": draft["revision"],
            "prompt": "测试",
            "idempotencyKey": "first",
            "modelRouteOverride": "builder-first",
        }
        first = await client.post(path + "/try-runs", headers=HEADERS, json=body)
        assert first.status_code == 202, first.text
        assert first.json()["run"]["input"]["model_route_override"] == "builder-first"
        models.resolve_runtime.assert_awaited_with(
            HEADERS["X-Tenant-ID"],
            draft["spec"]["name"],
            "builder-first",
            apply_agent_binding=False,
            required_api_format=None if runtime == "deepagents" else "anthropic_compatible",
        )
        await container.worker.execute(HEADERS["X-Tenant-ID"], first.json()["run"]["run_id"])
        second = await client.post(
            path + "/try-runs",
            headers=HEADERS,
            json={
                **body,
                "idempotencyKey": "second",
                "modelRouteOverride": "builder-second",
                "continueFromRunId": first.json()["run"]["run_id"],
            },
        )
        assert second.status_code == 202, second.text
        assert second.json()["run"]["session_id"] == first.json()["run"]["session_id"]
        assert second.json()["run"]["input"]["model_route_override"] == "builder-second"
        await container.worker.execute(HEADERS["X-Tenant-ID"], second.json()["run"]["run_id"])
        reset = await client.post(
            path + "/try-runs",
            headers=HEADERS,
            json={
                **body,
                "idempotencyKey": "reset",
                "modelRouteOverride": None,
                "continueFromRunId": second.json()["run"]["run_id"],
            },
        )
        assert reset.status_code == 202, reset.text
        assert "model_route_override" not in reset.json()["run"]["input"]
        assert (await client.get(path, headers=HEADERS)).json() == draft
        models.resolve_runtime.return_value = None
        rejected = await client.post(
            path + "/try-runs",
            headers=HEADERS,
            json={
                **body,
                "idempotencyKey": "disabled",
                "modelRouteOverride": "disabled-route",
            },
        )
        assert rejected.status_code == 409, rejected.text
