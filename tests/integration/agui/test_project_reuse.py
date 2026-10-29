from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from harness.api.app import create_memory_app
from harness.studio.models import DraftSkill
from harness.studio.skill_builder import SkillConversationReply, SkillConversationRequest

HEADERS = {"X-Tenant-ID": "tenant-a", "X-User-ID": "user-1"}
SKILL = {
    "name": "project-report",
    "description": "生成本项目业务报告时使用",
    "instructions": "核实输入资料。按输入时间范围生成报告，并列出来源。",
    "files": [{"path": "assets/report.md", "content": "# 报告\n## 来源\n"}],
}


async def setup(client: AsyncClient) -> tuple[str, str, str]:
    response = await client.post(
        "/v1/agents",
        headers=HEADERS,
        json={"path": str(Path("tests/fixtures/agents/echo-agent/agent.yaml"))},
    )
    assert response.status_code in (200, 201), response.text
    project = (
        await client.post("/v1/studio/projects", headers=HEADERS, json={"name": "复用验收"})
    ).json()
    thread = (
        await client.post(
            "/v1/agui/threads",
            headers=HEADERS,
            json={
                "project_id": project["projectId"],
                "agent_name": "echo-agent",
                "agent_version": "0.1.0",
            },
        )
    ).json()
    run_id = await turn(client, thread["thread_id"])
    return project["projectId"], thread["thread_id"], run_id


async def turn(client: AsyncClient, thread: str) -> str:
    response = await client.post(
        "/v1/agui?agent_name=echo-agent&agent_version=0.1.0",
        headers=HEADERS,
        json={
            "threadId": thread,
            "runId": f"turn-{thread}",
            "state": {},
            "messages": [{"id": "m1", "role": "user", "content": "整理输入，生成有来源的报告"}],
            "tools": [],
            "context": [],
            "forwardedProps": {},
        },
    )
    assert response.status_code == 200, response.text
    return response.headers["x-harness-run-id"]


@pytest.mark.asyncio
async def test_collect_result_is_idempotent_and_available_in_another_task():
    app = create_memory_app(auto_execute=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        project, _, run = await setup(client)
        artifact = (
            await client.post(
                f"/v1/runs/{run}/artifacts",
                headers=HEADERS,
                files={"file": ("report.md", b"approved report", "text/markdown")},
            )
        ).json()
        url = f"/v1/studio/projects/{project}"
        response = await client.post(
            f"{url}/results",
            headers=HEADERS,
            json={"artifactId": artifact["artifact_id"], "addToResources": True},
        )
        assert response.status_code == 201, response.text
        saved = response.json()
        assert (
            await client.post(
                f"{url}/results",
                headers=HEADERS,
                json={"artifactId": artifact["artifact_id"], "addToResources": True},
            )
        ).json() == saved
        resources = (await client.get(f"{url}/resources", headers=HEADERS)).json()
        assert [doc["inputArtifactId"] for doc in resources["documents"]] == [
            saved["inputArtifactId"]
        ]
        second = (
            await client.post(
                "/v1/agui/threads",
                headers=HEADERS,
                json={"project_id": project, "agent_name": "echo-agent", "agent_version": "0.1.0"},
            )
        ).json()
        new_run = await app.state.container.runs.get(
            "tenant-a", await turn(client, second["thread_id"])
        )
        assert new_run.input["project_document_ids"] == [saved["inputArtifactId"]]
        foreign = {**HEADERS, "X-User-ID": "intruder"}
        assert (
            await client.post(
                f"{url}/results",
                headers=foreign,
                json={"artifactId": artifact["artifact_id"], "addToResources": True},
            )
        ).status_code == 404


@pytest.mark.asyncio
async def test_project_skills_are_drafts_then_run_snapshots_and_never_edit_agents():
    app = create_memory_app(auto_execute=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        project, thread, _ = await setup(client)
        before = (await client.get("/v1/agents", headers=HEADERS)).json()
        url = f"/v1/studio/projects/{project}"
        response = await client.post(
            f"{url}/skills",
            headers=HEADERS,
            json={"displayName": "项目报告", "skill": SKILL, "sourceThreadId": thread},
        )
        assert response.status_code == 201, response.text
        skill = response.json()
        assert skill["enabled"] is False
        context = await app.state.container.projects.context_for_run("tenant-a", "user-1", project)
        assert not context.local_skill_snapshots
        response = await client.patch(
            f"{url}/skills/{skill['skillId']}",
            headers=HEADERS,
            json={"revision": 1, "enabled": True},
        )
        assert response.status_code == 200, response.text
        context = await app.state.container.projects.context_for_run("tenant-a", "user-1", project)
        assert len(context.local_skill_snapshots) == 1
        captured_hash = context.local_skill_snapshots[0].content_hash
        second = (
            await client.post(
                "/v1/agui/threads",
                headers=HEADERS,
                json={"project_id": project, "agent_name": "echo-agent", "agent_version": "0.1.0"},
            )
        ).json()
        run = await app.state.container.runs.get(
            "tenant-a", await turn(client, second["thread_id"])
        )
        assert run.input["project_skill_references"] == list(context.skill_references)
        assert run.input["project_skill_snapshots"][0]["content_hash"] == captured_hash
        await client.patch(
            f"{url}/skills/{skill['skillId']}",
            headers=HEADERS,
            json={"revision": 1, "enabled": False},
        )
        assert not (
            await app.state.container.projects.context_for_run("tenant-a", "user-1", project)
        ).local_skill_snapshots
        assert (await app.state.container.runs.get("tenant-a", run.run_id)).input[
            "project_skill_snapshots"
        ][0]["content_hash"] == captured_hash
        assert (await client.get("/v1/agents", headers=HEADERS)).json() == before
        foreign = {**HEADERS, "X-User-ID": "intruder"}
        assert (
            await client.patch(
                f"{url}/skills/{skill['skillId']}",
                headers=foreign,
                json={"revision": 1, "enabled": True},
            )
        ).status_code == 404
        assert (
            await client.patch(
                f"{url}/skills/{skill['skillId']}",
                headers=HEADERS,
                json={"revision": 2, "enabled": True},
            )
        ).status_code == 409
        other = (
            await client.post("/v1/studio/projects", headers=HEADERS, json={"name": "其他项目"})
        ).json()["projectId"]
        assert not (
            await app.state.container.projects.context_for_run("tenant-a", "user-1", other)
        ).local_skill_snapshots
        assert (
            await client.post(
                f"/v1/studio/projects/{other}/skills",
                headers=HEADERS,
                json={"displayName": "偷用", "skill": SKILL, "sourceThreadId": thread},
            )
        ).status_code == 404


@pytest.mark.asyncio
async def test_extraction_uses_visible_owned_transcript_and_does_not_save_or_activate():
    app = create_memory_app(auto_execute=True)
    calls: list[SkillConversationRequest] = []

    class Generator:
        async def respond(
            self, tenant: str, request: SkillConversationRequest
        ) -> SkillConversationReply:
            calls.append(request)
            return SkillConversationReply(
                status="ready", reply="已提炼为草稿", skill=DraftSkill.model_validate(SKILL)
            )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        project, thread, _ = await setup(client)
        app.state.container = replace(app.state.container, skill_conversation=Generator())
        response = await client.post(
            f"/v1/studio/projects/{project}/skills/extract",
            headers=HEADERS,
            json={"threadId": thread, "modelRoute": "chat"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["skill"]["name"] == "project-report"
        assert "整理输入" in "\n".join(item.content for item in calls[0].messages)
        assert calls[0].context.agent_name == "project-skill"
        assert (
            await client.get(f"/v1/studio/projects/{project}/resources", headers=HEADERS)
        ).json()["project"]["localSkills"] == []
        other = (
            await client.post("/v1/studio/projects", headers=HEADERS, json={"name": "其他项目"})
        ).json()["projectId"]
        assert (
            await client.post(
                f"/v1/studio/projects/{other}/skills/extract",
                headers=HEADERS,
                json={"threadId": thread, "modelRoute": "chat"},
            )
        ).status_code == 404
        assert len(calls) == 1


@pytest.mark.asyncio
async def test_web_search_respects_user_configuration_and_creates_no_chat_or_link():
    app = create_memory_app(auto_execute=True)
    calls: list[tuple[str, str]] = []

    class Web:
        async def client_for_user(self, tenant: str, user: str) -> Any:
            calls.append((tenant, user))

            async def search(query: str, **kwargs: Any) -> dict[str, Any]:
                return {
                    "results": [
                        {"url": "https://example.com/policy", "title": "政策", "content": "说明"}
                    ]
                }

            return SimpleNamespace(search=search)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        project, _, _ = await setup(client)
        app.state.container = replace(app.state.container, web_configurations=Web())
        url = f"/v1/studio/projects/{project}/web-search"
        before = (await client.get("/v1/agui/threads", headers=HEADERS)).json()
        response = await client.get(url, headers=HEADERS, params={"query": "公开政策"})
        assert response.status_code == 200, response.text
        assert response.json()["results"][0]["title"] == "政策"
        assert calls == [("tenant-a", "user-1")]
        assert (
            await client.get(url, headers=HEADERS, params={"query": "https://example.com/direct"})
        ).status_code == 200
        assert len(calls) == 1
        assert (
            await client.get(url, headers=HEADERS, params={"query": "http://127.0.0.1/private"})
        ).status_code == 400
        assert (await client.get("/v1/agui/threads", headers=HEADERS)).json() == before
        assert (
            await client.get(f"/v1/studio/projects/{project}/resources", headers=HEADERS)
        ).json()["links"] == []
        assert (
            await client.get(
                url, headers={**HEADERS, "X-User-ID": "intruder"}, params={"query": "公开政策"}
            )
        ).status_code == 404


@pytest.mark.asyncio
async def test_project_skill_rejects_secrets_and_duplicate_enabled_names():
    app = create_memory_app(auto_execute=True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        project, thread, _ = await setup(client)
        url = f"/v1/studio/projects/{project}/skills"
        body = {"displayName": "项目报告", "skill": SKILL, "sourceThreadId": thread}
        unsafe = {**SKILL, "files": [{"path": "credentials.json", "content": "private"}]}
        response = await client.post(url, headers=HEADERS, json={**body, "skill": unsafe})
        assert response.status_code == 409
        first = (await client.post(url, headers=HEADERS, json=body)).json()
        second = (await client.post(url, headers=HEADERS, json=body)).json()
        response = await client.patch(
            f"{url}/{first['skillId']}", headers=HEADERS, json={"revision": 1, "enabled": True}
        )
        assert response.status_code == 200
        response = await client.patch(
            f"{url}/{second['skillId']}", headers=HEADERS, json={"revision": 1, "enabled": True}
        )
        assert response.status_code == 409
