"""Builder tools operate on the authenticated preview, without an authoring model call."""

import json
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from harness.core.errors import ConflictError
from harness.core.models import Run
from harness.studio.runtime_builder import builder_overlay
from tests.integration.api.test_agent_studio_api import (
    SERVICE_TOKEN,
    app_and_container,
    draft_request,
)

HEADERS = {
    "Authorization": f"Bearer {SERVICE_TOKEN}",
    "X-Tenant-ID": "tenant-tools",
    "X-User-ID": "user-tools",
}


def content(reply):
    return json.loads(reply["content"][0]["text"])


@pytest.mark.asyncio
async def test_bound_tools_preview_apply_conflicts_and_normal_run_isolation():
    app, container = app_and_container()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=HEADERS
    ) as client:
        draft = (await client.post("/v1/studio/drafts", json=draft_request())).json()
        path = f"/v1/studio/drafts/{draft['draftId']}"
        response = await client.post(
            path + "/try-runs",
            json={
                "expectedRevision": 1,
                "prompt": "启用联网",
                "idempotencyKey": "builder-tools",
                "builderTools": True,
            },
        )
        assert response.status_code == 202, response.text
        run = Run.model_validate(response.json()["run"])
        session = await container.sessions.get(run.tenant_id, run.session_id)
        with patch.object(
            container.studio, "builder_context", wraps=container.studio.builder_context
        ) as read:
            overlay = await builder_overlay(container, container.event_service, run, session)
            read.assert_not_called()  # no catalog work on the ordinary-task path
            normal = run.model_copy(update={"input": {"prompt": "hello"}})
            assert not (
                await builder_overlay(container, container.event_service, normal, session)
            ).tools
        discard, reader, proposer = overlay.tools
        assert (await proposer.handler({}))["isError"]
        context = content(await reader.handler({}))
        assert context["expectedRevision"] == 1
        tools = list(dict.fromkeys([*draft["spec"]["builtinTools"], "WebSearch", "WebFetch"]))
        proposal_args = {
            "expectedRevision": 1,
            "explanation": "启用联网",
            "changes": {"builtinTools": tools},
        }
        assert (await proposer.handler({**proposal_args, "expectedRevision": 2}))["isError"]
        result = await proposer.handler(proposal_args)
        assert not result.get("isError"), result
        assert content(result)["status"] == "pending_review"
        assert (await client.get(path)).json() == draft  # actual proposal cannot mutate
        events = await container.events.list_after(run.tenant_id, run.run_id, 0)
        proposals = [event for event in events if event.type == "builder.proposal"]
        assert len(proposals) == 1
        proposed = proposals[0].payload
        applied = await client.post(
            path + "/builder-apply",
            json={
                "expectedRevision": 1,
                "changes": proposed["changes"],
            },
        )
        assert applied.status_code == 200, applied.text
        assert {"WebSearch", "WebFetch"} <= set(applied.json()["spec"]["builtinTools"])
        assert (await reader.handler({}))["isError"]  # no stale writes/proposals
        assert (await proposer.handler(proposal_args))["isError"]
        assert (await client.get(path)).json()["revision"] == 2
        # A normal/foreign agent session cannot acquire tools via a forged input marker.
        with pytest.raises(ConflictError):
            await builder_overlay(
                container,
                container.event_service,
                run,
                session.model_copy(update={"agent_version": "1.0.0"}),
            )
        foreign = await builder_overlay(
            container,
            container.event_service,
            run,
            session.model_copy(update={"user_id": "other-user"}),
        )
        assert (await foreign.tools[1].handler({}))["isError"]


@pytest.mark.asyncio
async def test_builder_continuation_cannot_reuse_a_plain_test_session():
    app, container = app_and_container()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=HEADERS
    ) as client:
        draft = (await client.post("/v1/studio/drafts", json=draft_request())).json()
        path = f"/v1/studio/drafts/{draft['draftId']}/try-runs"
        first = await client.post(
            path, json={"expectedRevision": 1, "prompt": "hello", "idempotencyKey": "plain"}
        )
        await container.worker.execute("tenant-tools", first.json()["run"]["run_id"])
        assert "studio_builder" not in first.json()["run"]["input"]
        unsupported = await client.post(
            path,
            json={
                "expectedRevision": 1,
                "prompt": "change",
                "idempotencyKey": "other",
                "builderTools": True,
                "continueFromRunId": first.json()["run"]["run_id"],
            },
        )
        assert unsupported.status_code == 409
        assert "Builder 工具权限已变化" in unsupported.text
