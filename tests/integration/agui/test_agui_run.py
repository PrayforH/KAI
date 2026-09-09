import asyncio
import json
import shutil
from pathlib import Path

import pytest
from ag_ui.core import RunAgentInput
from httpx import ASGITransport, AsyncClient

from harness.api.app import create_memory_app
from harness.core.errors import ConflictError, NotFoundError

FIXTURE_MANIFEST = Path("tests/fixtures/agents/echo-agent/agent.yaml")
HEADERS = {"X-Tenant-ID": "tenant-a", "X-User-ID": "user-1"}
PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def _request(*, thread_id: str, run_id: str, prompt: str) -> dict[str, object]:
    return {
        "threadId": thread_id,
        "runId": run_id,
        "state": {},
        "messages": [{"id": f"message-{run_id}", "role": "user", "content": prompt}],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }


def _request_with_prompts(*, thread_id: str, run_id: str, prompts: list[str]) -> dict[str, object]:
    request = _request(thread_id=thread_id, run_id=run_id, prompt=prompts[-1])
    request["messages"] = [
        {"id": f"message-{run_id}-{index}", "role": "user", "content": prompt}
        for index, prompt in enumerate(prompts)
    ]
    return request


def _events(body: str) -> list[dict[str, object]]:
    return [
        json.loads(line.removeprefix("data: "))
        for line in body.splitlines()
        if line.startswith("data: ")
    ]


@pytest.mark.asyncio
async def test_post_agui_runs_agent_and_streams_standard_events() -> None:
    app = create_memory_app(auto_execute=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        published = await client.post(
            "/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS
        )
        assert published.status_code == 201

        response = await client.post(
            "/v1/agui?agent_name=echo-agent&agent_version=0.1.0",
            json=_request(thread_id="thread-a", run_id="client-run-1", prompt="hello"),
            headers=HEADERS,
        )
        replay = await client.get(
            f"/v1/agui/runs/{response.headers['x-harness-run-id']}/events",
            headers=HEADERS,
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache, no-transform"
    events = _events(response.text)
    assert events[0] == {
        "type": "RUN_STARTED",
        "threadId": "thread-a",
        "runId": "client-run-1",
    }
    assert any(
        event.get("type") == "TEXT_MESSAGE_CONTENT" and event.get("delta") == "Echo: hello"
        for event in events
    )
    assert sum(event.get("type") == "ACTIVITY_SNAPSHOT" for event in events) == 1
    assert any(event.get("type") == "ACTIVITY_DELTA" for event in events)
    assert events[-1] == {
        "type": "RUN_FINISHED",
        "threadId": "thread-a",
        "runId": "client-run-1",
    }
    replay_events = _events(replay.text)
    assert replay_events[0] == events[0]
    assert replay_events[-1] == events[-1]


@pytest.mark.asyncio
async def test_post_agui_waits_for_terminal_event_after_run_status_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_memory_app(auto_execute=True)
    worker = app.state.container.worker
    event_service = worker._events
    original_append = event_service.append

    async def delayed_append(**values: object):
        if values.get("event_type") == "run.succeeded":
            await asyncio.sleep(0.1)
        return await original_append(**values)

    monkeypatch.setattr(event_service, "append", delayed_append)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        response = await client.post(
            "/v1/agui?agent_name=echo-agent&agent_version=0.1.0",
            json=_request(thread_id="thread-race", run_id="client-race", prompt="hello"),
            headers=HEADERS,
        )

    assert any(event.get("type") == "RUN_FINISHED" for event in _events(response.text))


@pytest.mark.asyncio
async def test_post_agui_reuses_harness_session_for_same_thread() -> None:
    app = create_memory_app(auto_execute=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        for run_id in ("client-run-1", "client-run-2"):
            response = await client.post(
                "/v1/agui?agent_name=echo-agent&agent_version=0.1.0",
                json=_request(thread_id="thread-a", run_id=run_id, prompt=run_id),
                headers=HEADERS,
            )
            assert response.status_code == 200

    first = await app.state.container.agui.get_binding(
        tenant_id="tenant-a", user_id="user-1", thread_id="thread-a"
    )
    second = await app.state.container.agui.get_binding(
        tenant_id="tenant-a", user_id="user-1", thread_id="thread-a"
    )
    assert first.session_id == second.session_id
    assert first.agent_name == "echo-agent"
    assert first.agent_version == "0.1.0"


@pytest.mark.asyncio
async def test_same_agent_can_switch_version_and_keep_thread_history(
    tmp_path: Path,
) -> None:
    app = create_memory_app(auto_execute=True)
    next_agent = tmp_path / "echo-agent"
    shutil.copytree(FIXTURE_MANIFEST.parent, next_agent)
    manifest = next_agent / "agent.yaml"
    manifest.write_text(
        manifest.read_text().replace("version: 0.1.0", "version: 0.1.1"),
        encoding="utf-8",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for path in (FIXTURE_MANIFEST, manifest):
            published = await client.post(
                "/v1/agents", json={"path": str(path)}, headers=HEADERS
            )
            assert published.status_code == 201

        first = await client.post(
            "/v1/agui?agent_name=echo-agent&agent_version=0.1.0",
            json=_request(thread_id="thread-upgrade", run_id="run-old", prompt="old"),
            headers=HEADERS,
        )
        second = await client.post(
            "/v1/agui?agent_name=echo-agent&agent_version=0.1.1",
            json=_request_with_prompts(
                thread_id="thread-upgrade",
                run_id="run-new",
                prompts=["old", "new"],
            ),
            headers=HEADERS,
        )
        history = await client.get(
            "/v1/agui/threads/thread-upgrade/history", headers=HEADERS
        )
        listed = await client.get("/v1/agui/threads", headers=HEADERS)

    assert first.status_code == 200
    assert second.status_code == 200
    binding = await app.state.container.agui._bindings.get_by_thread(
        "tenant-a", "user-1", "thread-upgrade"
    )
    assert len(binding.previous_session_ids) == 1
    assert binding.session_id not in binding.previous_session_ids
    assert [
        message["content"]
        for message in history.json()["messages"]
        if message["role"] == "user"
    ] == ["old", "new"]
    task = next(item for item in listed.json() if item["thread_id"] == "thread-upgrade")
    assert task["agent_version"] == "0.1.1"


@pytest.mark.asyncio
async def test_post_agui_deduplicates_active_request_and_returns_original_run() -> None:
    app = create_memory_app(auto_execute=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        original_request = RunAgentInput.model_validate(
            _request(
                thread_id="thread-deduplicate",
                run_id="client-original",
                prompt="生成 PPT",
            )
        )
        original = await app.state.container.agui.create_run(
            tenant_id="tenant-a",
            user_id="user-1",
            agent_name="echo-agent",
            agent_version="0.1.0",
            request=original_request,
        )
        worker_task = asyncio.create_task(
            app.state.container.worker.execute("tenant-a", original.run_id)
        )
        response = await client.post(
            "/v1/agui?agent_name=echo-agent&agent_version=0.1.0",
            json=_request(
                thread_id="thread-deduplicate",
                run_id="client-duplicate",
                prompt="生成 PPT",
            ),
            headers=HEADERS,
        )
        await worker_task

    assert response.status_code == 200
    assert response.headers["X-Harness-Run-Reused"] == "true"
    assert response.headers["X-Harness-Run-Deduplicated"] == "true"
    assert response.headers["X-Harness-Run-ID"] == original.run_id
    assert response.headers["X-Harness-Canonical-Client-Run-ID"] == "client-original"
    runs = await app.state.container.runs.list_for_sessions(
        "tenant-a", [original.session_id], limit=10
    )
    assert [item.run_id for item in runs] == [original.run_id]


@pytest.mark.asyncio
async def test_agui_thread_list_and_history_restore_owned_tasks() -> None:
    app = create_memory_app(auto_execute=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        for thread_id, prompt in (
            ("thread-history-a", "first task"),
            ("thread-history-b", "second task"),
        ):
            response = await client.post(
                "/v1/agui?agent_name=echo-agent&agent_version=0.1.0",
                json=_request(
                    thread_id=thread_id,
                    run_id=f"client-{thread_id}",
                    prompt=prompt,
                ),
                headers=HEADERS,
            )
            assert response.status_code == 200

        listed = await client.get("/v1/agui/threads", headers=HEADERS)
        tasks = {item["thread_id"]: item for item in listed.json()}
        run_id = tasks["thread-history-a"]["run_id"]
        artifact = await app.state.container.artifacts.upload(
            tenant_id="tenant-a",
            run_id=run_id,
            name="report.html",
            media_type="text/html",
            content=b"<h1>report</h1>",
        )
        history = await client.get("/v1/agui/threads/thread-history-a/history", headers=HEADERS)
        hidden = await client.get(
            "/v1/agui/threads/thread-history-a/history",
            headers={"X-Tenant-ID": "tenant-a", "X-User-ID": "user-2"},
        )

    assert listed.status_code == 200
    assert tasks["thread-history-a"]["title"] == "first task"
    assert tasks["thread-history-a"]["status"] == "succeeded"
    assert history.status_code == 200
    assert history.json()["status"] == "succeeded"
    assert history.json()["run_id"] == run_id
    messages = history.json()["messages"]
    assert messages[0] == {
        "id": f"user-{run_id}",
        "role": "user",
        "content": "first task",
    }
    assistant = messages[1]
    assert assistant["content"] == "Echo: first task"
    tool_calls = assistant["toolCalls"]
    assert [item["function"]["name"] for item in tool_calls] == [
        "harness_run_activity",
        "harness_present_artifact",
    ]
    artifact_arguments = json.loads(tool_calls[1]["function"]["arguments"])
    assert artifact_arguments["artifact_id"] == artifact.artifact_id
    assert artifact_arguments["name"] == "report.html"
    assert [item.get("toolCallId") for item in messages[2:]] == [
        f"harness-activity-{run_id}",
        f"harness-artifact-{artifact.artifact_id}",
    ]
    assert hidden.status_code == 404


@pytest.mark.asyncio
async def test_agui_thread_can_be_archived_restored_and_keeps_history() -> None:
    app = create_memory_app(auto_execute=True)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS
        )
        run = await client.post(
            "/v1/agui?agent_name=echo-agent&agent_version=0.1.0",
            json=_request(
                thread_id="thread-archive",
                run_id="client-run-archive",
                prompt="archive me",
            ),
            headers=HEADERS,
        )
        archived = await client.patch(
            "/v1/agui/threads/thread-archive",
            json={"archived": True},
            headers=HEADERS,
        )
        active_list = await client.get("/v1/agui/threads", headers=HEADERS)
        archive_list = await client.get(
            "/v1/agui/threads?archived=true", headers=HEADERS
        )
        history = await client.get(
            "/v1/agui/threads/thread-archive/history", headers=HEADERS
        )
        restored = await client.patch(
            "/v1/agui/threads/thread-archive",
            json={"archived": False},
            headers=HEADERS,
        )
        restored_list = await client.get("/v1/agui/threads", headers=HEADERS)

    assert run.status_code == 200
    assert archived.status_code == 200
    assert archived.json()["archived"] is True
    assert active_list.json() == []
    assert [item["thread_id"] for item in archive_list.json()] == ["thread-archive"]
    assert history.status_code == 200
    assert history.json()["messages"]
    assert restored.status_code == 200
    assert restored.json()["archived"] is False
    assert [item["thread_id"] for item in restored_list.json()] == ["thread-archive"]


@pytest.mark.asyncio
async def test_agui_history_restores_user_input_attachments() -> None:
    app = create_memory_app(auto_execute=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        upload = await client.post(
            "/v1/input-artifacts",
            files={
                "file": (
                    "quarterly-review.pptx",
                    b"presentation",
                    PPTX_MEDIA_TYPE,
                )
            },
            headers=HEADERS,
        )
        input_artifact_id = upload.json()["input_artifact_id"]
        request = _request(
            thread_id="thread-with-ppt",
            run_id="client-with-ppt",
            prompt="根据附件生成汇报",
        )
        request["messages"] = [
            {
                "id": "message-with-ppt",
                "role": "user",
                "content": [
                    {"type": "text", "text": "根据附件生成汇报"},
                    {
                        "type": "document",
                        "source": {
                            "type": "data",
                            "value": input_artifact_id,
                            "mimeType": PPTX_MEDIA_TYPE,
                        },
                        "metadata": {"filename": "quarterly-review.pptx"},
                    },
                ],
            }
        ]
        response = await client.post(
            "/v1/agui?agent_name=echo-agent&agent_version=0.1.0",
            json=request,
            headers=HEADERS,
        )
        history = await client.get("/v1/agui/threads/thread-with-ppt/history", headers=HEADERS)

    assert upload.status_code == 201
    assert response.status_code == 200
    user = history.json()["messages"][0]
    assert user["content"][0] == {
        "type": "text",
        "text": "根据附件生成汇报",
    }
    assert user["content"][1] == {
        "type": "document",
        "source": {
            "type": "url",
            "value": input_artifact_id,
            "mimeType": PPTX_MEDIA_TYPE,
        },
        "metadata": {"filename": "quarterly-review.pptx"},
    }


@pytest.mark.asyncio
async def test_edited_multiturn_branch_updates_title_and_hides_superseded_turn() -> None:
    app = create_memory_app(auto_execute=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        first = await client.post(
            "/v1/agui?agent_name=echo-agent&agent_version=0.1.0",
            json=_request(thread_id="thread-edit", run_id="run-greeting", prompt="你好"),
            headers=HEADERS,
        )
        original = await client.post(
            "/v1/agui?agent_name=echo-agent&agent_version=0.1.0",
            json=_request_with_prompts(
                thread_id="thread-edit",
                run_id="run-original",
                prompts=["你好", "写入长期记忆并生成报告"],
            ),
            headers=HEADERS,
        )
        edited = await client.post(
            "/v1/agui?agent_name=echo-agent&agent_version=0.1.0",
            json=_request_with_prompts(
                thread_id="thread-edit",
                run_id="run-edited",
                prompts=["你好", "不写入了，先单次生成可下载报告"],
            ),
            headers=HEADERS,
        )
        listed = await client.get("/v1/agui/threads", headers=HEADERS)
        history = await client.get("/v1/agui/threads/thread-edit/history", headers=HEADERS)

    assert first.status_code == original.status_code == edited.status_code == 200
    task = next(item for item in listed.json() if item["thread_id"] == "thread-edit")
    assert task["title"] == "生成可下载报告"
    messages = history.json()["messages"]
    user_messages = [item["content"] for item in messages if item["role"] == "user"]
    assistant_messages = [item["content"] for item in messages if item["role"] == "assistant"]
    assistant_activity_runs = [
        json.loads(item["toolCalls"][0]["function"]["arguments"])["activity"]["run_id"]
        for item in messages
        if item["role"] == "assistant"
    ]
    assert user_messages == ["你好", "不写入了，先单次生成可下载报告"]
    assert assistant_messages == [
        "Echo: 你好",
        "Echo: 不写入了，先单次生成可下载报告",
    ]
    assert len(assistant_activity_runs) == 2
    assert len(set(assistant_activity_runs)) == 2


@pytest.mark.asyncio
async def test_cancel_agui_run_resolves_protocol_ids_to_harness_run() -> None:
    app = create_memory_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        request = RunAgentInput.model_validate(
            _request(thread_id="thread-cancel", run_id="client-run-cancel", prompt="wait")
        )
        run = await app.state.container.agui.create_run(
            tenant_id="tenant-a",
            user_id="user-1",
            agent_name="echo-agent",
            agent_version="0.1.0",
            request=request,
        )

        response = await client.post(
            "/v1/agui/threads/thread-cancel/runs/client-run-cancel/cancel",
            headers=HEADERS,
        )

    assert response.status_code == 200
    assert response.json()["run_id"] == run.run_id
    assert response.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_resumed_agui_run_by_server_id() -> None:
    app = create_memory_app()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS
        )
        request = RunAgentInput.model_validate(
            _request(
                thread_id="thread-resumed",
                run_id="client-run-resumed",
                prompt="wait",
            )
        )
        run = await app.state.container.agui.create_run(
            tenant_id="tenant-a",
            user_id="user-1",
            agent_name="echo-agent",
            agent_version="0.1.0",
            request=request,
        )

        response = await client.post(
            f"/v1/agui/runs/{run.run_id}/cancel",
            headers=HEADERS,
        )

    assert response.status_code == 200
    assert response.json()["run_id"] == run.run_id
    assert response.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_agui_run_recovers_protocol_binding_after_api_restart() -> None:
    app = create_memory_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        request = RunAgentInput.model_validate(
            _request(thread_id="thread-restarted", run_id="client-run-restarted", prompt="wait")
        )
        run = await app.state.container.agui.create_run(
            tenant_id="tenant-a",
            user_id="user-1",
            agent_name="echo-agent",
            agent_version="0.1.0",
            request=request,
        )
        app.state.container.agui._run_bindings.clear()

        response = await client.post(
            "/v1/agui/threads/thread-restarted/runs/client-run-restarted/cancel",
            headers=HEADERS,
        )

    assert response.status_code == 200
    assert response.json()["run_id"] == run.run_id
    assert response.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_agui_run_persists_a_task_scoped_model_route_override() -> None:
    app = create_memory_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        body = _request(
            thread_id="thread-model-override",
            run_id="client-model-override",
            prompt="hello",
        )
        body["forwardedProps"] = {"modelRoute": "minimax-m3"}
        run = await app.state.container.agui.create_run(
            tenant_id="tenant-a",
            user_id="user-1",
            agent_name="echo-agent",
            agent_version="0.1.0",
            request=RunAgentInput.model_validate(body),
        )

    assert run.input["model_route_override"] == "minimax-m3"


@pytest.mark.asyncio
async def test_agui_run_rejects_an_invalid_model_route_override() -> None:
    app = create_memory_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        body = _request(
            thread_id="thread-model-invalid",
            run_id="client-model-invalid",
            prompt="hello",
        )
        body["forwardedProps"] = {"modelRoute": "../../secret"}

        with pytest.raises(ConflictError, match="model route override is invalid"):
            await app.state.container.agui.create_run(
                tenant_id="tenant-a",
                user_id="user-1",
                agent_name="echo-agent",
                agent_version="0.1.0",
                request=RunAgentInput.model_validate(body),
            )


@pytest.mark.asyncio
async def test_agui_run_resolves_uploaded_binary_input_for_owner() -> None:
    app = create_memory_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        uploaded = await client.post(
            "/v1/input-artifacts",
            files={"file": ("facts.txt", b"The code is amber-731.", "text/plain")},
            headers=HEADERS,
        )
        input_artifact_id = uploaded.json()["input_artifact_id"]

    body = _request(thread_id="thread-file", run_id="client-file", prompt="Read it")
    body["messages"] = [
        {
            "id": "message-file",
            "role": "user",
            "content": [
                {"type": "text", "text": "Read it"},
                {
                    "type": "binary",
                    "mimeType": "text/plain",
                    "id": input_artifact_id,
                    "filename": "facts.txt",
                },
                {
                    "type": "binary",
                    "mimeType": "text/plain",
                    "id": input_artifact_id,
                    "filename": "duplicate.txt",
                },
            ],
        }
    ]
    run = await app.state.container.agui.create_run(
        tenant_id="tenant-a",
        user_id="user-1",
        agent_name="echo-agent",
        agent_version="0.1.0",
        request=RunAgentInput.model_validate(body),
    )

    assert run.input == {
        "prompt": "Read it",
        "conversation_prompts": ["Read it"],
        "input_artifact_ids": [input_artifact_id],
    }


@pytest.mark.asyncio
async def test_agui_run_does_not_trust_inline_data_urls_or_cross_user_ids() -> None:
    app = create_memory_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        uploaded = await client.post(
            "/v1/input-artifacts",
            files={"file": ("private.txt", b"private", "text/plain")},
            headers=HEADERS,
        )
        input_artifact_id = uploaded.json()["input_artifact_id"]

    untrusted = _request(thread_id="thread-untrusted", run_id="untrusted", prompt="Read")
    untrusted["messages"] = [
        {
            "id": "message-untrusted",
            "role": "user",
            "content": [
                {"type": "text", "text": "Read"},
                {
                    "type": "binary",
                    "mimeType": "text/plain",
                    "url": "file:///Users/example/private.txt",
                    "data": "c2VjcmV0",
                    "filename": "private.txt",
                },
            ],
        }
    ]
    run = await app.state.container.agui.create_run(
        tenant_id="tenant-a",
        user_id="user-1",
        agent_name="echo-agent",
        agent_version="0.1.0",
        request=RunAgentInput.model_validate(untrusted),
    )
    assert run.input["input_artifact_ids"] == []

    forged = _request(thread_id="thread-forged", run_id="forged", prompt="Read")
    forged["messages"] = [
        {
            "id": "message-forged",
            "role": "user",
            "content": [
                {"type": "text", "text": "Read"},
                {
                    "type": "binary",
                    "mimeType": "text/plain",
                    "id": input_artifact_id,
                },
            ],
        }
    ]
    with pytest.raises(NotFoundError, match="input artifact not found"):
        await app.state.container.agui.create_run(
            tenant_id="tenant-a",
            user_id="user-2",
            agent_name="echo-agent",
            agent_version="0.1.0",
            request=RunAgentInput.model_validate(forged),
        )


@pytest.mark.asyncio
async def test_agui_run_accepts_assistant_ui_document_transport_envelope() -> None:
    app = create_memory_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        uploaded = await client.post(
            "/v1/input-artifacts",
            files={"file": ("report.pdf", b"fake pdf", "application/pdf")},
            headers=HEADERS,
        )
        input_artifact_id = uploaded.json()["input_artifact_id"]

    body = _request(thread_id="thread-document", run_id="document", prompt="Read")
    body["messages"] = [
        {
            "id": "message-document",
            "role": "user",
            "content": [
                {"type": "text", "text": "Read"},
                {
                    "type": "document",
                    "source": {
                        "type": "data",
                        "value": input_artifact_id,
                        "mimeType": "application/pdf",
                    },
                    "metadata": {"filename": "report.pdf"},
                },
            ],
        }
    ]

    run = await app.state.container.agui.create_run(
        tenant_id="tenant-a",
        user_id="user-1",
        agent_name="echo-agent",
        agent_version="0.1.0",
        request=RunAgentInput.model_validate(body),
    )

    assert run.input["input_artifact_ids"] == [input_artifact_id]


@pytest.mark.asyncio
async def test_agui_run_accepts_assistant_ui_image_transport_envelope() -> None:
    app = create_memory_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        uploaded = await client.post(
            "/v1/input-artifacts",
            files={"file": ("scan.jpg", b"fake image", "image/jpeg")},
            headers=HEADERS,
        )
        input_artifact_id = uploaded.json()["input_artifact_id"]

    body = _request(thread_id="thread-image", run_id="image", prompt="Inspect")
    body["messages"] = [
        {
            "id": "message-image",
            "role": "user",
            "content": [
                {"type": "text", "text": "Inspect"},
                {
                    "type": "image",
                    "source": {
                        "type": "data",
                        "value": input_artifact_id,
                        "mimeType": "image/jpeg",
                    },
                    "metadata": {"filename": "scan.jpg"},
                },
            ],
        }
    ]

    run = await app.state.container.agui.create_run(
        tenant_id="tenant-a",
        user_id="user-1",
        agent_name="echo-agent",
        agent_version="0.1.0",
        request=RunAgentInput.model_validate(body),
    )

    assert run.input["input_artifact_ids"] == [input_artifact_id]
    assert run.input["required_model_capabilities"] == ["vision"]


@pytest.mark.asyncio
async def test_history_keeps_messages_and_valid_files_when_old_attachment_is_missing() -> None:
    from harness.adapters.memory import InMemoryInputArtifactRepository

    app = create_memory_app(auto_execute=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await client.post("/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS)
        attachments = []
        ids = []
        for filename in ("missing.txt", "valid.txt"):
            upload = await client.post("/v1/input-artifacts",
                files={"file": (filename, b"historical attachment", "text/plain")}, headers=HEADERS)
            assert upload.status_code == 201
            input_id = upload.json()["input_artifact_id"]
            ids.append(input_id)
            attachments.append({"type": "document", "source": {
                "type": "data", "value": input_id, "mimeType": "text/plain"},
                "metadata": {"filename": filename}})
        request = _request(thread_id="old-files", run_id="old-run", prompt="保留历史对话")
        request["messages"] = [{"id": "m", "role": "user", "content": [
            {"type": "text", "text": "保留历史对话"}, *attachments]}]
        result = await client.post("/v1/agui?agent_name=echo-agent&agent_version=0.1.0",
            json=request, headers=HEADERS)
        assert result.status_code == 200
        repository = app.state.container.input_artifacts._repository
        assert isinstance(repository, InMemoryInputArtifactRepository)
        repository._items.pop(("tenant-a", ids[0]))
        history = await client.get("/v1/agui/threads/old-files/history", headers=HEADERS)
        assert history.status_code == 200
        messages = history.json()["messages"]
        assert len(messages) >= 2
        assert "保留历史对话" in messages[0]["content"][0]["text"]
        assert "部分历史附件已不可用" in messages[0]["content"][0]["text"]
        assert messages[0]["content"][1]["source"]["value"] == ids[1]
        denied = await client.get("/v1/agui/threads/old-files/history",
            headers={**HEADERS, "X-User-ID": "someone-else"})
        assert denied.status_code == 404


@pytest.mark.asyncio
async def test_wiki_without_knowledge_fails_before_creating_a_run() -> None:
    app = create_memory_app(auto_execute=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        published = await client.post(
            "/v1/agents", json={"path": str(FIXTURE_MANIFEST)}, headers=HEADERS
        )
        assert published.status_code == 201
        request = _request(thread_id="wiki-no-base", run_id="wiki-no-base", prompt="查询资料")
        request["forwardedProps"] = {"knowledgeMode": "wiki"}
        response = await client.post(
            "/v1/agui?agent_name=echo-agent&agent_version=0.1.0", json=request, headers=HEADERS
        )
    assert response.status_code == 409
    assert "Wiki 问答需要先选择知识库" in response.text
