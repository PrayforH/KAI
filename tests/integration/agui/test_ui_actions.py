"""UI admission must stay safe across retries, history and ordinary tool review."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest
from httpx import ASGITransport, AsyncClient, Response

from harness.api.app import create_app
from harness.api.dependencies import ApiContainer, build_memory_container
from harness.core.errors import PermissionDeniedError
from harness.core.models import AguiThreadBinding, RunStatus
from harness.core.ports import RunTask
from harness.presentation.models import ActionRequest, Publication
from harness.presentation.schema import Presentation
from harness.runtime.fake import FakeRuntime
from tests.support.policies import fake_runtime_review_profiles

H = {"X-Tenant-ID": "tenant-a", "X-User-ID": "owner"}
FIXTURE = Path("tests/fixtures/agents/echo-agent/agent.yaml")


async def setup(
    *, kind: str = "write", prompt: str = "请提交此项目", fields: list[dict[str, Any]] | None = None
) -> tuple[ApiContainer, Presentation]:
    container = build_memory_container(policy_profiles=fake_runtime_review_profiles())
    await container.agents.publish("tenant-a", "owner", FIXTURE)
    session = await container.sessions.create("tenant-a", "owner", "echo-agent", "0.1.0")
    fields = fields or [{"key": "project", "label": "项目", "type": "text", "required": True}]
    block = {
        "version": 1,
        "kind": "form",
        "fallback_text": "请填写项目",
        "data": {"fields": fields},
        "actions": [{"kind": kind, "label": "提交项目", "prompt": prompt}],
    }
    run = await container.runs.create(
        "tenant-a",
        session.session_id,
        "initial",
        input={
            "prompt": "测试\n```harness-ui\n"
            + json.dumps(block, ensure_ascii=False).replace("[approval]", "\\u005bapproval\\u005d")
            + "\n```"
        },
    )
    result = await container.worker.execute("tenant-a", run.run_id)
    presentations = await container.presentations.materialize(result)
    p = presentations[0]
    return container, p


def body(
    p: Presentation, values: dict[str, list[str]] | None = None, revision: int = 1
) -> dict[str, Any]:
    return {
        "action_id": p.actions[0]["action_id"],
        "expected_revision": revision,
        "values": values if values is not None else {"project": ["测试项目"]},
    }


async def post(
    c: AsyncClient,
    p: Presentation,
    *,
    key: str = "submit",
    values: dict[str, list[str]] | None = None,
    revision: int = 1,
    headers: dict[str, str] | None = None,
) -> Response:
    return await c.post(
        f"/v1/ui/presentations/{p.presentation_id}/actions",
        headers={**(headers or H), "Idempotency-Key": key},
        json=body(p, values, revision),
    )


@pytest.mark.asyncio
async def test_retries_and_new_browser_keys_do_not_duplicate_a_write() -> None:
    container, p = await setup()
    async with AsyncClient(
        transport=ASGITransport(app=create_app(container)), base_url="http://test"
    ) as c:
        responses = await asyncio.gather(*(post(c, p, key=f"click-{i}") for i in range(12)))
        assert all(r.status_code == 202 for r in responses)
        assert len({r.json()["run_id"] for r in responses}) == 1
        run_id = responses[0].json()["run_id"]
        before = cast(FakeRuntime, container.runtime).execution_count
        await container.worker.execute("tenant-a", run_id)
        await container.worker.execute("tenant-a", run_id)
        assert cast(FakeRuntime, container.runtime).execution_count == before + 1
        snapshot = await c.get(f"/v1/ui/presentations/{p.presentation_id}", headers=H)
        assert snapshot.json()["action_requests"][0]["execution_status"] == "succeeded"
        assert snapshot.json()["action_requests"][0]["submitted_values"] == {
            "project": ["测试项目"]
        }
        assert len(snapshot.json()["action_requests"]) == 1
        changed = await post(c, p, values={"project": ["另一个项目"]})
        assert changed.status_code == 409
        assert (
            await post(c, p, key="new-key", values={"project": ["另一个项目"]})
        ).status_code == 409


@pytest.mark.asyncio
async def test_cross_actor_and_tenant_requests_are_hidden() -> None:
    container, p = await setup()
    async with AsyncClient(
        transport=ASGITransport(app=create_app(container)), base_url="http://test"
    ) as c:
        for h in [{**H, "X-User-ID": "other"}, {**H, "X-Tenant-ID": "other"}]:
            assert (
                await c.get(f"/v1/ui/presentations/{p.presentation_id}", headers=h)
            ).status_code == 404
            assert (await post(c, p, headers=h)).status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("values", [{}, {"unknown": ["x"]}, {"project": ["a", "b"]}])
async def test_server_rejects_missing_or_undeclared_form_input(
    values: dict[str, list[str]],
) -> None:
    container, p = await setup()
    async with AsyncClient(
        transport=ASGITransport(app=create_app(container)), base_url="http://test"
    ) as c:
        assert (await post(c, p, values=values)).status_code == 422
        assert (
            await container.presentations.repository.requests_for_presentation(
                "tenant-a", "owner", p.presentation_id
            )
            == []
        )


@pytest.mark.asyncio
async def test_old_revision_conflicts_and_local_state_uses_cas() -> None:
    container, p = await setup()
    updated = p.model_copy(update={"revision": 2})
    await container.presentations.repository.revise("tenant-a", p.presentation_id, 1, updated)
    async with AsyncClient(
        transport=ASGITransport(app=create_app(container)), base_url="http://test"
    ) as c:
        assert (await post(c, p)).status_code == 409
        url = f"/v1/ui/presentations/{p.presentation_id}/view-state"
        request = {"revision": 1, "expected_version": 0, "state": {"values": {"project": ["草稿"]}}}
        assert (await c.put(url, headers=H, json=request)).status_code == 200
        assert (await c.put(url, headers=H, json=request)).status_code == 409
        old = (
            await c.get(f"/v1/ui/presentations/{p.presentation_id}?revision=1", headers=H)
        ).json()
        assert old["revision"] == 1
        assert old["view_state"]["state"]["values"]["project"] == ["草稿"]
        assert (await c.get(f"/v1/ui/presentations/{p.presentation_id}", headers=H)).json()[
            "revision"
        ] == 2


@pytest.mark.asyncio
async def test_ui_write_keeps_the_existing_approval_and_rejection_path() -> None:
    container, p = await setup(prompt="[approval] 请提交此项目")
    async with AsyncClient(
        transport=ASGITransport(app=create_app(container)), base_url="http://test"
    ) as c:
        response = await post(c, p)
        run_id = response.json()["run_id"]
        waiting = await container.worker.execute("tenant-a", run_id)
        assert waiting.status is RunStatus.WAITING_APPROVAL
        status = await c.get(
            f"/v1/ui/presentations/{p.presentation_id}/actions/{response.json()['request_id']}",
            headers=H,
        )
        assert status.json()["execution_status"] == "waiting_approval"
        events = await container.events.list_after("tenant-a", run_id, 0)
        approval_id = next(
            e.payload["approval_id"] for e in events if e.type == "approval.requested"
        )
        rejected = await c.put(
            f"/v1/approvals/{approval_id}", headers=H, json={"decision": "rejected"}
        )
        assert rejected.status_code == 200
        result = await container.worker.execute("tenant-a", run_id)
        assert result.status in {RunStatus.REJECTED, RunStatus.FAILED}
        assert await container.artifacts.list_for_run("tenant-a", run_id) == []


@pytest.mark.asyncio
async def test_read_action_cannot_turn_its_prompt_into_a_write_grant() -> None:
    container, p = await setup(kind="read", prompt="[approval] 写入一个文件")
    async with AsyncClient(
        transport=ASGITransport(app=create_app(container)), base_url="http://test"
    ) as c:
        response = await post(c, p)
        result = await container.worker.execute("tenant-a", response.json()["run_id"])
        events = await container.events.list_after("tenant-a", result.run_id, 0)
        assert any(
            e.type == "tool.result" and e.payload.get("error", {}).get("code") == "policy_denied"
            for e in events
        )
        assert not any(e.type == "approval.requested" for e in events)


@pytest.mark.asyncio
async def test_recovery_after_queue_failure_and_permission_revocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    container, p = await setup()
    original = container.task_queue.enqueue

    async def fail(task: RunTask) -> None:
        raise RuntimeError("queue temporarily unavailable")

    container.task_queue.enqueue = fail
    async with AsyncClient(
        transport=ASGITransport(app=create_app(container), raise_app_exceptions=False),
        base_url="http://test",
    ) as c:
        assert (await post(c, p)).status_code == 500
        stored = (
            await container.presentations.repository.requests_for_presentation(
                "tenant-a", "owner", p.presentation_id
            )
        )[0]
        expired = stored.model_copy(
            update={"lease_until": datetime.now(UTC) - timedelta(seconds=1)}
        )
        await container.presentations.repository.save_request(expired, stored.fencing_token)
        container.task_queue.enqueue = original
        await container.presentations.process_once()
        recovered = await container.presentations.repository.request("tenant-a", stored.request_id)
        assert recovered.run_id is not None
        assert recovered.status == "submitted"
        again = await post(c, p)
        assert again.json()["run_id"] == recovered.run_id

        async def deny(owner: Publication, request: ActionRequest) -> None:
            raise PermissionDeniedError("permission withdrawn")

        monkeypatch.setattr(container.presentations, "_authorize", deny)
        assert (await post(c, p, key="new")).status_code == 403


@pytest.mark.asyncio
async def test_unknown_expected_revision_is_a_conflict_without_admitting_an_action() -> None:
    container, p = await setup()
    async with AsyncClient(
        transport=ASGITransport(app=create_app(container)), base_url="http://test"
    ) as c:
        assert (await post(c, p, revision=999)).status_code == 409
    assert (
        await container.presentations.repository.requests_for_presentation(
            "tenant-a", "owner", p.presentation_id
        )
        == []
    )


@pytest.mark.asyncio
async def test_outbox_delivery_is_idempotent_and_excludes_form_values() -> None:
    container, p = await setup()
    notice = (await container.presentations.repository.outbox())[0]
    event = dict(notice)
    event.pop("outbox_id")
    assert container.event_service is not None
    await asyncio.gather(*(container.event_service.append_once(**event) for _ in range(5)))
    await container.presentations.flush_events()
    events = await container.events.list_after("tenant-a", p.run_id, 0)
    assert len([e for e in events if e.type == "ui.presentation.ready"]) == 1
    assert (await container.presentations.repository.outbox()) == []
    assert "values" not in notice["payload"]


@pytest.mark.asyncio
async def test_capabilities_intersect_the_server_catalog() -> None:
    container = build_memory_container()
    async with AsyncClient(
        transport=ASGITransport(app=create_app(container)), base_url="http://test"
    ) as c:
        result = await c.get("/v1/ui/capabilities?renderers=table/1,malicious/1", headers=H)
        assert result.status_code == 200
        assert [r["renderer_version"] for r in result.json()["renderers"]] == ["table/1"]


@pytest.mark.asyncio
async def test_automation_thread_reads_and_restores_its_presentation() -> None:
    """An automation result belongs to ``automation:{task}``, not the reader.

    The user-scoped thread binding that already exposes the task and its files
    must also authorize the interactive snapshot and per-user view state.
    """

    container = build_memory_container(policy_profiles=fake_runtime_review_profiles())
    await container.agents.publish("tenant-a", "owner", FIXTURE)
    session = await container.sessions.create(
        "tenant-a",
        "automation:daily-news",
        "echo-agent",
        "0.1.0",
        api_key_id="daily-news",
        agent_owner_user_id="owner",
    )
    block = {
        "version": 1,
        "kind": "table",
        "title": "今日新闻",
        "fallback_text": "今日共两条新闻。",
        "data": {
            "columns": [{"key": "title", "label": "标题"}],
            "rows": [{"title": "第一条"}, {"title": "第二条"}],
        },
    }
    run = await container.runs.create(
        "tenant-a",
        session.session_id,
        "automation-ui",
        input={
            "prompt": "每日简报\n```harness-ui\n" + json.dumps(block, ensure_ascii=False) + "\n```"
        },
    )
    terminal = await container.worker.execute("tenant-a", run.run_id)
    p = (await container.presentations.materialize(terminal))[0]
    assert (await container.presentations.repository.get("tenant-a", p.presentation_id))[
        0
    ].owner_user_id == "automation:daily-news"
    now = datetime.now(UTC)
    await container.agui._bindings.add(
        AguiThreadBinding(
            tenant_id="tenant-a",
            user_id="owner",
            thread_id="daily-news",
            session_id=session.session_id,
            title="每日 AI 新闻推送",
            created_at=now,
            updated_at=now,
        )
    )

    async with AsyncClient(
        transport=ASGITransport(app=create_app(container)), base_url="http://test"
    ) as c:
        snapshot = await c.get(f"/v1/ui/presentations/{p.presentation_id}", headers=H)
        assert snapshot.status_code == 200
        assert snapshot.json()["presentation_id"] == p.presentation_id
        assert snapshot.json()["view_state"]["state"] == {}
        state_url = f"/v1/ui/presentations/{p.presentation_id}/view-state"
        saved = await c.put(
            state_url,
            headers=H,
            json={"revision": 1, "expected_version": 0, "state": {"sort": None}},
        )
        assert saved.status_code == 200 and saved.json()["version"] == 1
        restored = await c.get(f"/v1/ui/presentations/{p.presentation_id}", headers=H)
        assert restored.json()["view_state"] == {"version": 1, "state": {"sort": None}}
        for headers in ({**H, "X-User-ID": "other"}, {**H, "X-Tenant-ID": "other"}):
            assert (
                await c.get(f"/v1/ui/presentations/{p.presentation_id}", headers=headers)
            ).status_code == 404
            assert (
                await c.put(
                    state_url,
                    headers=headers,
                    json={"revision": 1, "expected_version": 1, "state": {"sort": None}},
                )
            ).status_code == 404
