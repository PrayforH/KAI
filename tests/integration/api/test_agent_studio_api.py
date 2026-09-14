import asyncio
import hashlib
import json
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Any, cast
from zipfile import ZipFile

import httpx
import pytest
import yaml
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from PIL import Image
from pydantic import SecretStr

from harness.adapters.memory import InMemoryAgentRegistry
from harness.api.app import create_app
from harness.api.dependencies import ApiContainer, build_memory_container
from harness.auth.models import Membership
from harness.auth.repositories import InMemoryAuthRepository
from harness.config import Settings
from harness.core.errors import NotFoundError
from harness.core.manifest import ToolDirectorySnapshot
from harness.evals.models import EvalRunStatus
from harness.quota.models import QuotaResource, ReplaceQuotaPolicyRequest
from harness.sharing.models import WorkspaceAgentStatus
from harness.studio.catalog import default_capability_catalog
from harness.studio.mcp_discovery import (
    DiscoveredServer,
    McpDiscoveryService,
)

SERVICE_TOKEN = "studio-service-token-with-at-least-32-characters"


def image_bytes(*, format: str = "PNG") -> bytes:
    output = BytesIO()
    Image.new("RGB", (256, 256), color="white").save(output, format=format)
    return output.getvalue()


def app() -> FastAPI:
    container = replace(
        build_memory_container(),
        environment="production",
        api_bearer_token=SecretStr(SERVICE_TOKEN),
    )
    return create_app(container)


def app_and_container(*, auto_execute: bool = False) -> tuple[FastAPI, ApiContainer]:
    container = replace(
        build_memory_container(auto_execute=auto_execute),
        environment="production",
        api_bearer_token=SecretStr(SERVICE_TOKEN),
    )
    return create_app(container), container


async def register(client: AsyncClient, email: str) -> dict[str, Any]:
    response = await client.post(
        "/v1/auth/register",
        json={
            "email": email,
            "password": "SecurePass123",
            "display_name": "Studio Builder",
        },
    )
    assert response.status_code == 201, response.text
    return cast(dict[str, Any], response.json())


def draft_request(name: str = "policy-researcher") -> dict[str, str]:
    return {
        "name": name,
        "domain": "policy-research",
        "displayName": "政策研究助手",
        "description": "整理政策材料并输出有出处的研究结论。",
        "template": "analyst",
    }


def tavily_resource() -> dict[str, Any]:
    return default_capability_catalog().mcp_servers[0].model_dump(mode="json", by_alias=True)


async def drain_eval(container: ApiContainer, eval_run_id: str) -> None:
    for _ in range(40):
        await container.eval_controller.process_once()
        task = await container.task_queue.dequeue()
        if task is not None:
            await container.worker.execute(task.tenant_id, task.run_id)
            await container.task_queue.acknowledge(task)
        current = await container.eval_run_repository.get("tenant-a", eval_run_id)
        if current.status.is_terminal:
            return
    raise AssertionError("Eval Run did not converge")


@pytest.mark.asyncio
async def test_studio_rejects_unauthenticated_and_self_reported_identity() -> None:
    async with AsyncClient(transport=ASGITransport(app=app()), base_url="http://test") as client:
        anonymous = await client.get("/v1/studio/capabilities")
        spoofed = await client.get(
            "/v1/studio/capabilities",
            headers={"X-Tenant-ID": "tenant-evil", "X-User-ID": "user-evil"},
        )

    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "api_auth_required"
    assert spoofed.status_code == 401
    assert spoofed.json()["error"]["code"] == "api_auth_required"


@pytest.mark.asyncio
async def test_service_identity_can_build_and_publish_existing_bundle() -> None:
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "builder-a",
    }
    async with AsyncClient(transport=ASGITransport(app=app()), base_url="http://test") as client:
        capabilities = await client.get("/v1/studio/capabilities", headers=headers)
        created = await client.post("/v1/studio/drafts", headers=headers, json=draft_request())
        draft_id = created.json()["draftId"]
        validation = await client.post(f"/v1/studio/drafts/{draft_id}/validate", headers=headers)
        bundle = await client.get(f"/v1/studio/drafts/{draft_id}/bundle", headers=headers)
        nexau = await client.get(f"/v1/studio/drafts/{draft_id}/nexau-bundle", headers=headers)
        published = await client.post(f"/v1/studio/drafts/{draft_id}/publish", headers=headers)
        drafts = await client.get("/v1/studio/drafts", headers=headers)

    assert capabilities.status_code == 200
    assert [item["reference"] for item in capabilities.json()["mcpServers"]] == ["tavily-readonly"]
    assert created.status_code == 201
    assert created.json()["tenantId"] == "tenant-a"
    assert created.json()["createdBy"] == "builder-a"
    assert validation.status_code == 200
    assert validation.json()["ready"] is True
    assert validation.json()["contract"]["sandbox"] == "isolated"
    assert bundle.status_code == 200
    assert bundle.headers["content-type"] == "application/zip"
    content_hash = validation.json()["contentHash"]
    package_hash = validation.json()["packageHash"]
    assert bundle.headers["content-disposition"] == (
        f'attachment; filename="policy-researcher-0.1.0-{package_hash[:12]}.zip"'
    )
    archive_hash = hashlib.sha256(bundle.content).hexdigest()
    assert bundle.headers["etag"] == f'"{archive_hash}"'
    assert bundle.headers["x-agent-content-sha256"] == content_hash
    assert bundle.headers["x-agent-package-sha256"] == package_hash
    assert nexau.status_code == 200
    assert nexau.headers["x-agent-export-format"] == "nexau"
    assert nexau.headers["content-disposition"] == (
        'attachment; filename="policy-researcher-0.1.0-nexau.zip"'
    )
    with ZipFile(BytesIO(nexau.content)) as archive:
        assert {"nexau.json", "agent.yaml", "systemprompt.md"}.issubset(archive.namelist())
        manifest = json.loads(archive.read("nexau.json"))
        config = yaml.safe_load(archive.read("agent.yaml"))
        assert manifest["agents"] == {config["name"]: "agent.yaml"}
    assert published.status_code == 200
    assert published.json()["name"] == "policy-researcher"
    assert "snapshot" not in published.json()
    assert drafts.json()[0]["publishedVersion"] == "0.1.0"
    assert drafts.json()[0]["goal"] == "整理政策材料并输出有出处的研究结论。"
    assert drafts.json()[0]["primaryOutput"] == "可核验的完成结果与必要交付物"
    assert drafts.json()[0]["skillCount"] == 0
    assert drafts.json()[0]["toolCount"] == 5
    assert drafts.json()[0]["networkToolsEnabled"] is False


@pytest.mark.asyncio
async def test_capabilities_are_the_runtime_compatibility_source_of_truth() -> None:
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-runtime-contract",
        "X-User-ID": "builder-a",
    }
    async with AsyncClient(transport=ASGITransport(app=app()), base_url="http://test") as client:
        response = await client.get("/v1/studio/capabilities", headers=headers)

    assert response.status_code == 200
    runtimes = {item["runtime"]: item for item in response.json()["runtimeCapabilities"]}
    assert {"claude-agent-sdk", "codex-app-server"} == set(runtimes)
    assert {"mcp_http", "subagents"} <= set(runtimes["codex-app-server"]["capabilities"])
    assert "openai_compatible" in runtimes["codex-app-server"]["modelApiFormats"]


@pytest.mark.asyncio
async def test_server_templates_keep_delegation_opt_in() -> None:
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-template-contract",
        "X-User-ID": "builder-a",
    }
    async with AsyncClient(transport=ASGITransport(app=app()), base_url="http://test") as client:
        created = []
        for template in ("analyst", "operator", "orchestrator"):
            body = draft_request(f"{template}-agent")
            body["template"] = template
            response = await client.post("/v1/studio/drafts", headers=headers, json=body)
            assert response.status_code == 201, response.text
            created.append(response.json())

    specs = {item["spec"]["template"]: item["spec"] for item in created}
    assert specs["analyst"]["taskContract"]["goal"]
    assert {"Write", "Bash"} <= set(specs["analyst"]["builtinTools"])
    assert specs["analyst"]["permissionPolicy"] == "production-standard"
    assert "Bash" in specs["operator"]["builtinTools"]
    assert {"Write", "Bash"} <= set(specs["orchestrator"]["builtinTools"])
    assert "Task" not in specs["orchestrator"]["builtinTools"]
    assert specs["orchestrator"]["subagents"] == []


@pytest.mark.asyncio
async def test_delete_personal_agent_hides_catalog_and_preserves_release_history() -> None:
    application, container = app_and_container()
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-delete-agent",
        "X-User-ID": "builder-a",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        created = await client.post(
            "/v1/studio/drafts",
            headers=headers,
            json=draft_request("delete-me"),
        )
        draft = created.json()
        published = await client.post(
            f"/v1/studio/drafts/{draft['draftId']}/publish",
            headers=headers,
            json={"expectedRevision": 1},
        )
        deleted = await client.delete(
            f"/v1/studio/drafts/{draft['draftId']}",
            headers=headers,
            params={"expectedRevision": 2},
        )
        missing = await client.get(f"/v1/studio/drafts/{draft['draftId']}", headers=headers)
        catalog = await client.get("/v1/agents", headers=headers)

    assert created.status_code == 201, created.text
    assert published.status_code == 200, published.text
    assert deleted.status_code == 204, deleted.text
    assert missing.status_code == 404
    assert all(item["name"] != "delete-me" for item in catalog.json())
    preserved = await container.agents.get_published(
        "tenant-delete-agent", "builder-a", "delete-me", "0.1.0"
    )
    assert preserved.name == "delete-me"
    identity = await container.workspace_agents.get_agent("tenant-delete-agent", draft["agentId"])
    assert identity.status is WorkspaceAgentStatus.ARCHIVED
    assert identity.current_version is None


@pytest.mark.asyncio
async def test_delete_agent_requires_current_revision_and_no_subagent_dependents() -> None:
    application = app()
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-delete-dependent",
        "X-User-ID": "builder-a",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        child = await client.post(
            "/v1/studio/drafts",
            headers=headers,
            json=draft_request("child-agent"),
        )
        stale = await client.delete(
            f"/v1/studio/drafts/{child.json()['draftId']}",
            headers=headers,
            params={"expectedRevision": 2},
        )
        parent = await client.post(
            "/v1/studio/drafts",
            headers=headers,
            json={**draft_request("lead-agent"), "template": "orchestrator"},
        )
        parent_spec = parent.json()["spec"]
        parent_spec["builtinTools"].append("Task")
        parent_spec["subagents"] = [
            {
                "alias": "child",
                "ref": "child-agent@0.1.0",
                "responsibility": "处理委派任务",
            }
        ]
        updated = await client.put(
            f"/v1/studio/drafts/{parent.json()['draftId']}",
            headers=headers,
            json={"expectedRevision": 1, "spec": parent_spec},
        )
        blocked = await client.delete(
            f"/v1/studio/drafts/{child.json()['draftId']}",
            headers=headers,
            params={"expectedRevision": 1},
        )

    assert stale.status_code == 409
    assert "revision changed" in stale.json()["error"]["message"]
    assert updated.status_code == 200, updated.text
    assert blocked.status_code == 409
    assert "lead-agent" not in blocked.json()["error"]["message"]
    assert "政策研究助手" in blocked.json()["error"]["message"]


@pytest.mark.asyncio
async def test_task_driven_builder_compiles_codex_draft_from_tenant_capabilities() -> None:
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-task-builder",
        "X-User-ID": "builder-a",
    }
    async with AsyncClient(transport=ASGITransport(app=app()), base_url="http://test") as client:
        catalog_response = await client.get("/v1/studio/catalog", headers=headers)
        catalog_record = catalog_response.json()
        catalog = catalog_record["catalog"]
        catalog["modelRoutes"][0]["apiFormat"] = "openai_compatible"
        replaced = await client.put(
            "/v1/studio/catalog",
            headers=headers,
            json={"expectedRevision": catalog_record["revision"], "catalog": catalog},
        )
        assert replaced.status_code == 200, replaced.text
        created = await client.post(
            "/v1/studio/drafts/from-task",
            headers=headers,
            json={
                "task": "搜索最新互联网舆情，分析风险并生成可下载报告。",
                "sampleInput": "分析今日样本并生成报告",
                "runtimePreference": "auto",
            },
        )
        duplicate = await client.post(
            "/v1/studio/drafts/from-task",
            headers=headers,
            json={
                "task": "搜索最新互联网舆情，分析风险并生成可下载报告。",
                "runtimePreference": "auto",
            },
        )

    assert created.status_code == 201, created.text
    payload = created.json()
    spec = payload["draft"]["spec"]
    recommendation = payload["recommendation"]
    assert spec["runtime"] == "codex-app-server"
    assert spec["model"]["routeId"] == "deepseek-v4-flash"
    assert spec["template"] == "operator"
    assert {"Write", "Edit", "Bash"} <= set(spec["builtinTools"])
    assert spec["mcpServers"] == []
    assert spec["name"] == "sentiment-analyst"
    assert spec["displayName"] == "互联网舆情助手"
    assert spec["skills"] == []
    assert len(spec["evaluationCases"]) == 3
    assert recommendation["validation"]["ready"] is True
    assert recommendation["runtime"] == "codex-app-server"
    assert duplicate.status_code == 201, duplicate.text
    assert duplicate.json()["draft"]["spec"]["name"] == "sentiment-analyst-2"


@pytest.mark.asyncio
async def test_task_driven_builder_treats_office_assistant_as_writable() -> None:
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-office-builder",
        "X-User-ID": "builder-a",
    }
    async with AsyncClient(transport=ASGITransport(app=app()), base_url="http://test") as client:
        created = await client.post(
            "/v1/studio/drafts/from-task",
            headers=headers,
            json={
                "task": "办公文档助手，各种 Office 能力，可以做 PPT、写 Word、转换 Excel 图表。",
                "runtimePreference": "auto",
            },
        )

    assert created.status_code == 201, created.text
    spec = created.json()["draft"]["spec"]
    assert spec["template"] == "operator"
    assert spec["permissionPolicy"] == "production-standard"
    assert {"Write", "Edit", "Bash"} <= set(spec["builtinTools"])
    assert spec["mcpServers"] == []


@pytest.mark.asyncio
async def test_agent_builder_patch_is_review_only_and_contains_three_eval_classes() -> None:
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-builder-contract",
        "X-User-ID": "builder-a",
    }
    async with AsyncClient(transport=ASGITransport(app=app()), base_url="http://test") as client:
        created = await client.post("/v1/studio/drafts", headers=headers, json=draft_request())
        draft_id = created.json()["draftId"]
        patch = await client.post(
            f"/v1/studio/drafts/{draft_id}/builder-patch",
            headers=headers,
            json={
                "expectedRevision": 1,
                "goal": "分析政策材料并输出可验证结论",
                "audience": "政策研究员",
                "inputs": ["政策材料", "分析范围"],
                "outputs": ["结论", "证据索引"],
                "constraints": ["缺少证据时不得编造"],
                "examples": [],
            },
        )
        unchanged = await client.get(f"/v1/studio/drafts/{draft_id}", headers=headers)

    assert patch.status_code == 200, patch.text
    assert patch.json()["baseRevision"] == 1
    assert {case["tags"][0] for case in patch.json()["evaluationCases"]} == {
        "happy",
        "ambiguous",
        "safety",
    }
    assert unchanged.json()["revision"] == 1
    assert unchanged.json()["spec"]["systemPrompt"] != patch.json()["systemPrompt"]


@pytest.mark.asyncio
async def test_studio_try_run_executes_validated_snapshot_without_publishing() -> None:
    application, container = app_and_container(auto_execute=True)
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-try-run",
        "X-User-ID": "builder-a",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        created = await client.post(
            "/v1/studio/drafts", headers=headers, json=draft_request("try-run-agent")
        )
        draft_id = created.json()["draftId"]
        started = await client.post(
            f"/v1/studio/drafts/{draft_id}/try-runs",
            headers=headers,
            json={
                "expectedRevision": 1,
                "prompt": "请回显试跑结果",
                "idempotencyKey": "try-run-r1",
            },
        )
        view = await client.get(
            f"/v1/studio/drafts/{draft_id}/try-runs/{started.json()['run']['run_id']}",
            headers=headers,
            params={"draftRevision": 1},
        )
        event_stream = await client.get(
            f"/v1/studio/drafts/{draft_id}/try-runs/{started.json()['run']['run_id']}/events",
            headers=headers,
            params={"draftRevision": 1},
        )

    assert started.status_code == 202, started.text
    assert view.status_code == 200, view.text
    assert view.json()["run"]["status"] == "succeeded"
    assert "Echo: 请回显试跑结果" in view.json()["finalText"]
    assert event_stream.status_code == 200, event_stream.text
    assert event_stream.headers["content-type"].startswith("text/event-stream")
    assert "event: message.delta" in event_stream.text
    assert "event: run.succeeded" in event_stream.text
    assert [stage["id"] for stage in view.json()["loop"]] == [
        "plan",
        "tools",
        "correction",
        "verification",
        "result",
    ]
    assert view.json()["loop"][2]["status"] == "skipped"
    assert view.json()["loop"][4]["status"] == "completed"
    assert await container.agents.list_published("tenant-try-run", "builder-a") == []


@pytest.mark.asyncio
async def test_try_run_and_solidify_accept_an_unpublished_subagent_graph() -> None:
    application, container = app_and_container(auto_execute=True)
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-preview-graph",
        "X-User-ID": "builder-a",
    }
    child_request = draft_request("helper-agent")
    parent_request = {
        **draft_request("preview-lead"),
        "template": "orchestrator",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        child = await client.post("/v1/studio/drafts", headers=headers, json=child_request)
        child_spec = child.json()["spec"]
        child_spec["version"] = "1.0.0"
        child = await client.put(
            f"/v1/studio/drafts/{child.json()['draftId']}",
            headers=headers,
            json={"expectedRevision": 1, "spec": child_spec},
        )
        parent = await client.post("/v1/studio/drafts", headers=headers, json=parent_request)
        parent_id = parent.json()["draftId"]
        parent_spec = parent.json()["spec"]
        parent_spec["builtinTools"].append("Task")
        parent_spec["subagents"] = [
            {
                "alias": "helper",
                "ref": "helper-agent@1.0.0",
                "responsibility": "完成委派的专家分析任务",
            }
        ]
        parent = await client.put(
            f"/v1/studio/drafts/{parent_id}",
            headers=headers,
            json={"expectedRevision": 1, "spec": parent_spec},
        )
        started = await client.post(
            f"/v1/studio/drafts/{parent_id}/try-runs",
            headers=headers,
            json={
                "expectedRevision": 2,
                "prompt": "委派专家后汇总结果",
                "idempotencyKey": "preview-graph-r1",
            },
        )
        assert started.status_code == 202, started.text
        run_id = started.json()["run"]["run_id"]
        view = started
        for _ in range(20):
            view = await client.get(
                f"/v1/studio/drafts/{parent_id}/try-runs/{run_id}",
                headers=headers,
                params={"draftRevision": 2},
            )
            if view.json()["run"]["status"] in {"succeeded", "failed"}:
                break
            await asyncio.sleep(0)
        solidified = await client.post(
            f"/v1/studio/drafts/{parent_id}/solidify",
            headers=headers,
            json={"expectedRevision": 2, "draftRevision": 2, "runId": run_id},
        )

    assert child.status_code == 200, child.text
    assert parent.status_code == 200, parent.text
    assert started.status_code == 202, started.text
    assert view.json()["run"]["status"] == "succeeded"
    assert solidified.status_code == 200, solidified.text
    versions = await container.agents.list_published("tenant-preview-graph", "builder-a")
    assert {(item.name, item.version) for item in versions} == {
        ("helper-agent", "1.0.0"),
        ("preview-lead", "0.1.0"),
    }


@pytest.mark.asyncio
async def test_successful_try_run_solidifies_release_and_required_eval_baseline() -> None:
    application, container = app_and_container(auto_execute=True)
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-solidify",
        "X-User-ID": "builder-a",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        created = await client.post(
            "/v1/studio/drafts", headers=headers, json=draft_request("solid-agent")
        )
        draft_id = created.json()["draftId"]
        started = await client.post(
            f"/v1/studio/drafts/{draft_id}/try-runs",
            headers=headers,
            json={
                "expectedRevision": 1,
                "prompt": "完成固化验证",
                "idempotencyKey": "solid-run-r1",
            },
        )
        run_id = started.json()["run"]["run_id"]
        solidified = await client.post(
            f"/v1/studio/drafts/{draft_id}/solidify",
            headers=headers,
            json={"expectedRevision": 1, "draftRevision": 1, "runId": run_id},
        )
        retry = await client.post(
            f"/v1/studio/drafts/{draft_id}/solidify",
            headers=headers,
            json={"expectedRevision": 1, "draftRevision": 1, "runId": run_id},
        )

    assert solidified.status_code == 200, solidified.text
    payload = solidified.json()
    assert payload["version"]["status"] == "published"
    assert payload["draft"]["publishedVersion"] == "0.1.0"
    assert payload["dataset"]["required"] is True
    assert payload["dataset"]["sourceDraftRevision"] == 1
    assert [stage["id"] for stage in payload["loop"]] == [
        "plan",
        "tools",
        "correction",
        "verification",
        "result",
    ]
    assert retry.status_code == 200, retry.text
    assert retry.json()["dataset"]["version"] == payload["dataset"]["version"]
    versions = await container.agents.list_published("tenant-solidify", "builder-a")
    assert len(versions) == 1


@pytest.mark.asyncio
async def test_studio_manages_mcp_credentials_without_returning_secret_values() -> None:
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "builder-a",
    }
    secret = "credential-that-must-never-be-returned"
    other_headers = {**headers, "X-User-ID": "builder-b"}
    async with AsyncClient(transport=ASGITransport(app=app()), base_url="http://test") as client:
        configured = await client.put(
            "/v1/studio/mcp/company-search/credentials",
            headers=headers,
            json={"authKey": "authorization", "value": secret},
        )
        listed = await client.get("/v1/studio/mcp/credentials", headers=headers)
        hidden = await client.get("/v1/studio/mcp/credentials", headers=other_headers)
        deleted = await client.delete("/v1/studio/mcp/company-search/credentials", headers=headers)

    assert configured.status_code == 200
    assert configured.json()["configured"] is True
    assert configured.json()["keyNames"] == ["authorization"]
    assert listed.json()[0]["reference"] == "company-search"
    assert hidden.json() == []
    assert deleted.json() == {
        "reference": "company-search",
        "configured": False,
        "keyNames": [],
        "revision": None,
        "updatedBy": None,
        "updatedAt": None,
    }
    assert secret not in configured.text
    assert secret not in listed.text


@pytest.mark.asyncio
async def test_user_can_permanently_delete_an_unreferenced_personal_mcp() -> None:
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-delete-resource",
        "X-User-ID": "builder-a",
    }
    resource = {
        **tavily_resource(),
        "reference": "company-knowledge",
        "serverName": "company_knowledge",
        "label": "企业知识库",
        "category": "knowledge",
        "authMode": "none",
        "credentialReference": None,
    }
    async with AsyncClient(transport=ASGITransport(app=app()), base_url="http://test") as client:
        initial = await client.get("/v1/studio/catalog", headers=headers)
        created = await client.put(
            "/v1/studio/catalog/mcp/company-knowledge",
            headers=headers,
            json={
                "expectedRevision": initial.json()["revision"],
                "resource": resource,
                "allowedExecutionProfileIds": ["isolated-default"],
            },
        )
        deleted = await client.delete(
            "/v1/studio/catalog/mcp/company-knowledge/permanent",
            headers=headers,
            params={"expected_revision": created.json()["record"]["revision"]},
        )

    assert created.status_code == 200, created.text
    assert deleted.status_code == 200, deleted.text
    assert "company-knowledge" not in {
        item["reference"] for item in deleted.json()["record"]["catalog"]["mcpServers"]
    }


@pytest.mark.asyncio
async def test_tavily_has_the_same_edit_and_delete_controls_as_other_mcp() -> None:
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-delete-tavily",
        "X-User-ID": "builder-a",
    }
    async with AsyncClient(transport=ASGITransport(app=app()), base_url="http://test") as client:
        initial = await client.get("/v1/studio/catalog", headers=headers)
        deleted = await client.delete(
            "/v1/studio/catalog/mcp/tavily-readonly/permanent",
            headers=headers,
            params={"expected_revision": initial.json()["revision"]},
        )

    assert deleted.status_code == 200, deleted.text
    assert "tavily-readonly" not in {
        item["reference"] for item in deleted.json()["record"]["catalog"]["mcpServers"]
    }


@pytest.mark.asyncio
async def test_authenticated_mcp_requires_the_current_users_credential() -> None:
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-personal-required",
        "X-User-ID": "builder-a",
    }
    async with AsyncClient(transport=ASGITransport(app=app()), base_url="http://test") as client:
        initial = await client.get("/v1/studio/catalog", headers=headers)
        body = {
            "expectedRevision": initial.json()["revision"],
            "resource": tavily_resource(),
            "allowedExecutionProfileIds": ["isolated-default"],
        }
        rejected = await client.put(
            "/v1/studio/catalog/mcp/tavily-readonly",
            headers=headers,
            json=body,
        )
        configured = await client.put(
            "/v1/studio/mcp/tavily-readonly/credentials",
            headers=headers,
            json={"authKey": "api_key", "value": "personal-tavily-key"},
        )
        saved = await client.put(
            "/v1/studio/catalog/mcp/tavily-readonly",
            headers=headers,
            json=body,
        )

    assert rejected.status_code == 409
    assert "current user's credential" in rejected.text
    assert configured.status_code == 200
    assert saved.status_code == 200, saved.text
    assert saved.json()["record"]["catalog"]["mcpServers"][0]["ownerUserId"] == "builder-a"


@pytest.mark.asyncio
async def test_studio_bundle_import_creates_a_complete_editable_agent() -> None:
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "builder-a",
    }
    async with AsyncClient(transport=ASGITransport(app=app()), base_url="http://test") as client:
        created = await client.post(
            "/v1/studio/drafts",
            headers=headers,
            json=draft_request("portable-agent"),
        )
        source = created.json()
        source["spec"]["description"] = "可无损导入并继续编辑的完整 Agent。"
        source["spec"]["executionProfile"] = "isolated-default"
        saved = await client.put(
            f"/v1/studio/drafts/{source['draftId']}",
            headers=headers,
            json={"expectedRevision": 1, "spec": source["spec"]},
        )
        bundle = await client.get(
            f"/v1/studio/drafts/{source['draftId']}/bundle",
            headers=headers,
        )
        imported = await client.post(
            "/v1/studio/drafts/import",
            headers={**headers, "Content-Type": "application/zip"},
            content=bundle.content,
        )
        invalid_media = await client.post(
            "/v1/studio/drafts/import",
            headers={**headers, "Content-Type": "application/json"},
            content=bundle.content,
        )
        invalid_archive = await client.post(
            "/v1/studio/drafts/import",
            headers={**headers, "Content-Type": "application/zip"},
            content=b"not-a-zip",
        )

    assert saved.status_code == 200, saved.text
    assert bundle.status_code == 200, bundle.text
    assert imported.status_code == 201, imported.text
    payload = imported.json()
    assert payload["lossless"] is True
    assert payload["roundTripVerified"] is True
    assert payload["warnings"] == []
    assert payload["draft"]["draftId"] != source["draftId"]
    assert payload["draft"]["publishedVersion"] is None
    assert payload["draft"]["spec"] == saved.json()["spec"]
    assert payload["sourceContentHash"] == bundle.headers["x-agent-content-sha256"]
    assert payload["sourcePackageHash"] == bundle.headers["x-agent-package-sha256"]
    assert invalid_media.status_code == 415
    assert invalid_media.json()["error"]["code"] == "bundle_import_media_type_invalid"
    assert invalid_archive.status_code == 422
    assert invalid_archive.json()["error"]["code"] == "bundle_import_invalid"


@pytest.mark.asyncio
async def test_studio_imports_a_skill_archive_without_executing_its_scripts() -> None:
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "builder-a",
        "Content-Type": "application/zip",
    }
    archive = BytesIO()
    with ZipFile(archive, "w") as bundle:
        bundle.writestr(
            "ppt-master/SKILL.md",
            (
                "---\nname: ppt-master\ndescription: Build presentations.\n---\n\n"
                "Generate and verify a presentation in the workspace.\n"
            ),
        )
        bundle.writestr("ppt-master/scripts/render.py", "print('render')\n")

    async with AsyncClient(
        transport=ASGITransport(app=app()),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/v1/studio/skills/import?filename=ppt-master.zip",
            headers=headers,
            content=archive.getvalue(),
        )

    assert response.status_code == 200
    assert response.json()["skill"]["name"] == "ppt-master"
    assert response.json()["riskLevel"] == "review"
    assert response.json()["findings"] == ["包含可执行脚本：scripts/render.py"]


@pytest.mark.asyncio
async def test_studio_lists_and_installs_a_platform_skill_as_a_draft_snapshot() -> None:
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "builder-a",
    }
    async with AsyncClient(transport=ASGITransport(app=app()), base_url="http://test") as client:
        catalog = await client.get("/v1/studio/skills/catalog", headers=headers)
        created = await client.post(
            "/v1/studio/drafts",
            headers=headers,
            json=draft_request("platform-skill-agent"),
        )
        package = catalog.json()["packages"][0]
        installed = await client.post(
            f"/v1/studio/drafts/{created.json()['draftId']}/skills/catalog/"
            f"{package['packageId']}/install",
            headers=headers,
            json={"expectedRevision": 1, "packageRevision": package["revision"]},
        )
        bundle = await client.get(
            f"/v1/studio/drafts/{created.json()['draftId']}/bundle",
            headers=headers,
        )

    assert catalog.status_code == 200, catalog.text
    assert catalog.json()["revision"] == 1
    assert len(catalog.json()["packages"]) == 22
    assert created.json()["spec"]["skills"] == []
    assert installed.status_code == 200, installed.text
    installed_skill = installed.json()["draft"]["spec"]["skills"][0]
    assert installed_skill["source"] == {
        "kind": "platform",
        "packageId": package["packageId"],
        "packageRevision": package["revision"],
        "sourceUrl": package["sourceUrl"],
        "sourceRevision": package["sourceRevision"],
        "license": package["license"],
        "contentHash": package["contentHash"],
        "modified": False,
    }
    assert installed.json()["sourceContentHash"] == package["contentHash"]
    with ZipFile(BytesIO(bundle.content)) as archive:
        skill_md = archive.read(f"skills/{package['skill']['name']}/SKILL.md").decode()
    assert package["packageId"] in skill_md
    assert package["contentHash"] in skill_md


@pytest.mark.asyncio
async def test_skill_references_resolve_into_bundle_without_snapshot_install() -> None:
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "builder-a",
    }
    async with AsyncClient(transport=ASGITransport(app=app()), base_url="http://test") as client:
        created = await client.post(
            "/v1/studio/drafts",
            headers=headers,
            json=draft_request("skill-reference-agent"),
        )
        updated = await client.put(
            f"/v1/studio/drafts/{created.json()['draftId']}/skills/references",
            headers=headers,
            json={"expectedRevision": 1, "references": ["minimax-docx", "minimax-pdf"]},
        )
        bundle = await client.get(
            f"/v1/studio/drafts/{created.json()['draftId']}/bundle",
            headers=headers,
        )

    assert created.status_code == 201, created.text
    assert updated.status_code == 200, updated.text
    body = updated.json()
    assert body["spec"]["skillReferences"] == ["minimax-docx", "minimax-pdf"]
    # References stay declarative: no snapshot copies appear in the draft spec.
    assert body["spec"]["skills"] == []
    with ZipFile(BytesIO(bundle.content)) as archive:
        docx_skill = archive.read("skills/minimax-docx/SKILL.md").decode()
        manifest = __import__("yaml").safe_load(archive.read("agent.yaml").decode())
    assert "OpenXML" in docx_skill or "docx" in docx_skill
    resolved = [
        entry
        for entry in manifest["spec"]["skills"]
        if entry.endswith(("minimax-docx", "minimax-pdf"))
    ]
    assert len(resolved) == 2


@pytest.mark.asyncio
async def test_platform_skill_install_rejects_a_stale_package_revision() -> None:
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "builder-a",
    }
    async with AsyncClient(transport=ASGITransport(app=app()), base_url="http://test") as client:
        created = await client.post(
            "/v1/studio/drafts",
            headers=headers,
            json=draft_request("stale-platform-skill-agent"),
        )
        response = await client.post(
            f"/v1/studio/drafts/{created.json()['draftId']}/skills/catalog/"
            "evidence-reporting/install",
            headers=headers,
            json={"expectedRevision": 1, "packageRevision": 999},
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "draft_conflict"


@pytest.mark.asyncio
async def test_studio_installs_large_skill_without_returning_every_file_content() -> None:
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "builder-a",
    }
    archive = BytesIO()
    with ZipFile(archive, "w") as bundle:
        bundle.writestr(
            "ppt-master/SKILL.md",
            (
                "---\nname: ppt-master\ndescription: Build presentations.\n---\n\n"
                "Generate and verify a presentation in the workspace.\n"
            ),
        )
        for index in range(205):
            bundle.writestr(f"ppt-master/references/item-{index:03d}.md", f"item {index}\n")
        bundle.writestr("ppt-master/assets/preview.png", b"\x89PNG\r\n\x1a\npreview")

    async with AsyncClient(
        transport=ASGITransport(app=app()),
        base_url="http://test",
    ) as client:
        created = await client.post(
            "/v1/studio/drafts",
            headers=headers,
            json=draft_request("large-skill-agent"),
        )
        installed = await client.post(
            f"/v1/studio/drafts/{created.json()['draftId']}/skills/import"
            "?filename=ppt-master.zip&expectedRevision=1",
            headers={**headers, "Content-Type": "application/zip"},
            content=archive.getvalue(),
        )
        payload = installed.json()
        installed_skill = next(
            skill for skill in payload["draft"]["spec"]["skills"] if skill["name"] == "ppt-master"
        )
        installed_skill["description"] = "Updated without resending hidden files."
        saved = await client.put(
            f"/v1/studio/drafts/{created.json()['draftId']}",
            headers=headers,
            json={
                "expectedRevision": payload["draft"]["revision"],
                "spec": payload["draft"]["spec"],
            },
        )
        exported = await client.get(
            f"/v1/studio/drafts/{created.json()['draftId']}/bundle",
            headers=headers,
        )

    assert installed.status_code == 200, installed.text
    assert payload["fileCount"] == 206
    assert payload["binaryFileCount"] == 1
    assert installed_skill["fileCount"] == 206
    assert installed_skill["filesTruncated"] is True
    assert len(installed_skill["files"]) == 200
    assert saved.status_code == 200, saved.text
    with ZipFile(BytesIO(exported.content)) as bundle:
        names = set(bundle.namelist())
    assert "skills/ppt-master/references/item-204.md" in names
    assert "skills/ppt-master/assets/preview.png" in names


@pytest.mark.asyncio
async def test_studio_api_round_trips_and_bundles_on_demand_tool_directory() -> None:
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "builder-a",
    }
    async with AsyncClient(
        transport=ASGITransport(app=app()),
        base_url="http://test",
    ) as client:
        catalog = await client.get("/v1/studio/catalog", headers=headers)
        credential = await client.put(
            "/v1/studio/mcp/tavily-readonly/credentials",
            headers=headers,
            json={"authKey": "api_key", "value": "personal-tavily-key"},
        )
        registered = await client.put(
            "/v1/studio/catalog/mcp/tavily-readonly",
            headers=headers,
            json={
                "expectedRevision": catalog.json()["revision"],
                "resource": tavily_resource(),
                "allowedExecutionProfileIds": ["isolated-default"],
            },
        )
        route = await client.put(
            "/v1/studio/catalog/modelRoute/on-demand-test",
            headers=headers,
            json={
                "expectedRevision": registered.json()["record"]["revision"],
                "resource": {
                    "routeId": "on-demand-test",
                    "label": "On-demand test route",
                    "provider": "test",
                    "models": ["deepseek-v4-pro"],
                    "capabilities": ["streaming", "tool_use", "tool_search"],
                    "credentialReference": "NEW_API_KEY",
                },
            },
        )
        created = await client.post(
            "/v1/studio/drafts",
            headers=headers,
            json=draft_request("on-demand-agent"),
        )
        spec = created.json()["spec"]
        spec["model"] = {
            **spec["model"],
            "routeId": "on-demand-test",
            "model": "deepseek-v4-pro",
            "requiredCapabilities": [
                "streaming",
                "tool_use",
                "tool_search",
            ],
        }
        spec["mcpServers"] = ["tavily-readonly"]
        spec["toolExposureMode"] = "on_demand"
        replaced = await client.put(
            f"/v1/studio/drafts/{created.json()['draftId']}",
            headers=headers,
            json={"expectedRevision": 1, "spec": spec},
        )
        validation = await client.post(
            f"/v1/studio/drafts/{created.json()['draftId']}/validate",
            headers=headers,
        )
        bundle = await client.get(
            f"/v1/studio/drafts/{created.json()['draftId']}/bundle",
            headers=headers,
        )

    assert credential.status_code == 200, credential.text
    assert registered.status_code == 200, registered.text
    assert route.status_code == 200, route.text
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["spec"]["toolExposureMode"] == "on_demand"
    assert validation.status_code == 200, validation.text
    assert validation.json()["ready"] is True
    assert validation.json()["contract"]["toolExposureMode"] == "on_demand"
    assert validation.json()["contract"]["toolDirectoryEntries"] == 7
    with ZipFile(BytesIO(bundle.content)) as archive:
        directory = ToolDirectorySnapshot.model_validate_json(archive.read("tool-directory.json"))
    assert directory.exposure_mode == "on_demand"
    assert directory.content_hash == directory.digest()
    assert {entry.name for entry in directory.entries if entry.source == "mcp"} == {
        "mcp__tavily__tavily_search",
        "mcp__tavily__tavily_extract",
    }


@pytest.mark.asyncio
async def test_deployment_api_promotes_and_environment_sessions_pin_snapshot() -> None:
    application, _container = app_and_container(auto_execute=True)
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "release-manager",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        created = await client.post(
            "/v1/studio/drafts",
            headers=headers,
            json=draft_request("deployed-agent"),
        )
        draft_id = created.json()["draftId"]
        published = await client.post(f"/v1/studio/drafts/{draft_id}/publish", headers=headers)
        promoted = await client.post(
            "/v1/studio/deployments/promote",
            headers=headers,
            json={
                "agentName": "deployed-agent",
                "agentVersion": published.json()["version"],
                "environment": "production",
                "expectedEnvironmentRevision": 0,
                "canaryPercent": 100,
                "imageDigest": "sha256:" + "a" * 64,
                "executionProfile": "isolated-default",
                "config": {"LOG_LEVEL": "info"},
                "idempotencyKey": "api-first-release",
            },
        )
        deployment_id = promoted.json()["deployment"]["deploymentId"]
        deployment = await client.get(f"/v1/studio/deployments/{deployment_id}", headers=headers)
        environments = await client.get(
            "/v1/studio/agents/deployed-agent/environments", headers=headers
        )
        production = next(item for item in environments.json() if item["name"] == "production")
        policy = {
            **production["resourcePolicy"],
            "quota": {
                "maxRunBudgetUsd": 0.75,
                "maxModelTokens": 120_000,
                "maxArtifactBytes": 2_048,
            },
        }
        policy_updated = await client.put(
            "/v1/studio/agents/deployed-agent/environments/production/policy",
            headers=headers,
            json={
                "expectedEnvironmentRevision": production["revision"],
                "policy": policy,
            },
        )
        session = await client.post(
            "/v1/sessions",
            headers=headers,
            json={"agent_name": "deployed-agent", "environment": "production"},
        )

    assert promoted.status_code == 202
    assert deployment.json()["deployment"]["status"] == "succeeded"
    assert production["revision"] == 1
    assert production["routes"][0]["weight"] == 100
    assert policy_updated.status_code == 200
    assert policy_updated.json()["revision"] == 2
    assert policy_updated.json()["policyRevision"] == 2
    assert policy_updated.json()["policyHash"] != production["policyHash"]
    assert session.status_code == 201
    assert session.json()["agent_version"] == published.json()["version"]
    assert session.json()["deployment_snapshot_id"] == production["healthySnapshotId"]
    assert session.json()["environment_snapshot"]["policyRevision"] == 2
    assert (
        session.json()["environment_snapshot"]["resourcePolicy"]["quota"]["maxRunBudgetUsd"] == 0.75
    )


@pytest.mark.asyncio
async def test_webhook_trigger_is_secret_scoped_idempotent_and_disableable() -> None:
    application, container = app_and_container(auto_execute=True)
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "release-manager",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        created = await client.post(
            "/v1/studio/drafts",
            headers=headers,
            json=draft_request("webhook-agent"),
        )
        draft_id = created.json()["draftId"]
        published = await client.post(
            f"/v1/studio/drafts/{draft_id}/publish",
            headers=headers,
        )
        promoted = await client.post(
            "/v1/studio/deployments/promote",
            headers=headers,
            json={
                "agentName": "webhook-agent",
                "agentVersion": published.json()["version"],
                "environment": "production",
                "expectedEnvironmentRevision": 0,
                "canaryPercent": 100,
                "imageDigest": "sha256:" + "b" * 64,
                "executionProfile": "isolated-default",
                "config": {},
                "idempotencyKey": "webhook-agent-release",
            },
        )
        trigger_created = await client.post(
            "/v1/studio/agents/webhook-agent/triggers",
            headers=headers,
            json={"name": "外部工单入口", "environment": "production"},
        )
        trigger = trigger_created.json()["trigger"]
        secret = trigger_created.json()["secret"]
        trigger_id = trigger["triggerId"]
        descriptor = await client.get(f"/webhooks/agent-triggers/{trigger_id}/openapi.json")
        cached_descriptor = await client.get(
            f"/webhooks/agent-triggers/{trigger_id}/openapi.json",
            headers={"If-None-Match": descriptor.headers["etag"]},
        )
        invocation_headers = {
            "Authorization": f"Bearer {secret}",
            "Idempotency-Key": "external-ticket-42",
        }
        first = await client.post(
            f"/webhooks/agent-triggers/{trigger_id}",
            headers=invocation_headers,
            json={"prompt": "分析工单 42 [artifact]"},
        )
        retry = await client.post(
            f"/webhooks/agent-triggers/{trigger_id}",
            headers=invocation_headers,
            json={"prompt": "分析工单 42 [artifact]"},
        )
        conflict = await client.post(
            f"/webhooks/agent-triggers/{trigger_id}",
            headers=invocation_headers,
            json={"prompt": "这是另一个请求"},
        )
        status_response = await client.get(
            f"/webhooks/agent-triggers/{trigger_id}/runs/{first.json()['runId']}",
            headers={"Authorization": f"Bearer {secret}"},
        )
        event_stream = await client.get(
            f"/webhooks/agent-triggers/{trigger_id}/runs/{first.json()['runId']}/events",
            headers={"Authorization": f"Bearer {secret}"},
        )
        artifacts = await container.artifacts.list_for_run(
            "tenant-a",
            first.json()["runId"],
        )
        artifact_download = await client.get(
            f"/webhooks/agent-triggers/{trigger_id}/runs/{first.json()['runId']}"
            f"/artifacts/{artifacts[0].artifact_id}/content",
            headers={"Authorization": f"Bearer {secret}"},
        )
        wrong_protocol = await client.post(
            f"/a2a/agent-triggers/{trigger_id}/message:send",
            headers={
                "Authorization": f"Bearer {secret}",
                "A2A-Version": "1.0",
                "Content-Type": "application/a2a+json",
            },
            content=json.dumps(
                {
                    "message": {
                        "messageId": "wrong-protocol",
                        "role": "ROLE_USER",
                        "parts": [{"text": "不应运行"}],
                    },
                    "configuration": {"returnImmediately": True},
                }
            ),
        )
        invalid = await client.post(
            f"/webhooks/agent-triggers/{trigger_id}",
            headers={
                "Authorization": "Bearer invalid-secret",
                "Idempotency-Key": "external-ticket-43",
            },
            json={"prompt": "不应运行"},
        )
        listed = await client.get(
            "/v1/studio/agents/webhook-agent/triggers",
            headers=headers,
        )
        rotated = await client.post(
            f"/v1/studio/triggers/{trigger_id}/rotate-secret",
            headers=headers,
            json={"expectedRevision": listed.json()[0]["revision"]},
        )
        rotated_secret = rotated.json()["secret"]
        old_secret = await client.post(
            f"/webhooks/agent-triggers/{trigger_id}",
            headers={
                "Authorization": f"Bearer {secret}",
                "Idempotency-Key": "external-ticket-44",
            },
            json={"prompt": "旧密钥不应运行"},
        )
        disabled = await client.put(
            f"/v1/studio/triggers/{trigger_id}",
            headers=headers,
            json={
                "expectedRevision": rotated.json()["trigger"]["revision"],
                "name": "外部工单入口",
                "enabled": False,
            },
        )
        disabled_invoke = await client.post(
            f"/webhooks/agent-triggers/{trigger_id}",
            headers={
                "Authorization": f"Bearer {rotated_secret}",
                "Idempotency-Key": "external-ticket-45",
            },
            json={"prompt": "禁用后不应运行"},
        )

    assert promoted.status_code == 202, promoted.text
    assert trigger_created.status_code == 201, trigger_created.text
    assert len(secret) >= 32
    assert "secretDigest" not in trigger
    assert descriptor.status_code == 200
    assert descriptor.json()["info"]["version"] == published.json()["version"]
    assert descriptor.json()["x-agent-studio"]["lifecycle"] == (
        "Session -> Run -> Event -> Artifact"
    )
    assert cached_descriptor.status_code == 304
    assert first.status_code == 202, first.text
    assert first.json()["runId"] == retry.json()["runId"]
    assert first.json()["sessionId"] == retry.json()["sessionId"]
    assert first.json()["deploymentSnapshotId"] == promoted.json()["target"]["snapshotId"]
    assert conflict.status_code == 409
    assert status_response.status_code == 200
    assert status_response.json()["input"]["trigger_id"] == trigger_id
    assert "event: run.succeeded" in event_stream.text
    assert artifact_download.status_code == 200
    assert artifact_download.content == b"fake runtime artifact"
    assert wrong_protocol.status_code == 401
    assert invalid.status_code == 401
    assert len(listed.json()) == 1
    assert "secret" not in listed.json()[0]
    assert "secretDigest" not in listed.json()[0]
    assert rotated.status_code == 200
    assert rotated_secret != secret
    assert old_secret.status_code == 401
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert disabled_invoke.status_code == 401


@pytest.mark.asyncio
async def test_a2a_chatops_schedule_and_platform_mcp_use_existing_control_plane() -> None:
    application, container = app_and_container(auto_execute=False)
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "platform-admin",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        created = await client.post(
            "/v1/studio/drafts",
            headers=headers,
            json=draft_request("interop-agent"),
        )
        published = await client.post(
            f"/v1/studio/drafts/{created.json()['draftId']}/publish",
            headers=headers,
        )
        promoted = await client.post(
            "/v1/studio/deployments/promote",
            headers=headers,
            json={
                "agentName": "interop-agent",
                "agentVersion": published.json()["version"],
                "environment": "production",
                "expectedEnvironmentRevision": 0,
                "canaryPercent": 100,
                "imageDigest": "sha256:" + "c" * 64,
                "executionProfile": "isolated-default",
                "config": {},
                "idempotencyKey": "interop-release",
            },
        )
        await container.deployment_controller.drain_locally(
            "tenant-a",
            promoted.json()["deployment"]["deploymentId"],
        )
        a2a_created = await client.post(
            "/v1/studio/agents/interop-agent/triggers",
            headers=headers,
            json={
                "name": "A2A",
                "environment": "production",
                "kind": "a2a",
            },
        )
        a2a = a2a_created.json()
        trigger_id = a2a["trigger"]["triggerId"]
        secret = a2a["secret"]
        card = await client.get(f"/a2a/agent-triggers/{trigger_id}/agent-card.json")
        a2a_headers = {
            "Authorization": f"Bearer {secret}",
            "A2A-Version": "1.0",
            "Content-Type": "application/a2a+json",
        }
        sent = await client.post(
            f"/a2a/agent-triggers/{trigger_id}/message:send",
            headers=a2a_headers,
            json={
                "message": {
                    "messageId": "a2a-message-1",
                    "role": "ROLE_USER",
                    "parts": [{"text": "执行互操作任务"}],
                },
                "configuration": {"returnImmediately": True},
            },
        )
        assert sent.status_code == 200, sent.text
        task_id = sent.json()["task"]["id"]
        task = await client.get(
            f"/a2a/agent-triggers/{trigger_id}/tasks/{task_id}",
            headers=a2a_headers,
        )
        cancelled = await client.post(
            f"/a2a/agent-triggers/{trigger_id}/tasks/{task_id}:cancel",
            headers=a2a_headers,
        )
        chatops_created = await client.post(
            "/v1/studio/agents/interop-agent/triggers",
            headers=headers,
            json={
                "name": "ChatOps",
                "environment": "production",
                "kind": "chatops",
                "chatops": {
                    "provider": "generic",
                    "allowedChannelIds": ["ops"],
                },
            },
        )
        chatops_id = chatops_created.json()["trigger"]["triggerId"]
        chatops = await client.post(
            f"/chatops/agent-triggers/{chatops_id}",
            headers={"Authorization": f"Bearer {chatops_created.json()['secret']}"},
            json={
                "messageId": "chat-message-1",
                "channelId": "ops",
                "actorId": "operator",
                "text": "执行 ChatOps 任务",
            },
        )
        schedule = await client.post(
            "/v1/studio/agents/interop-agent/triggers",
            headers=headers,
            json={
                "name": "Hourly",
                "environment": "production",
                "kind": "schedule",
                "schedule": {
                    "intervalSeconds": 3600,
                    "timezone": "Asia/Shanghai",
                    "prompt": "生成小时报告",
                },
            },
        )
        mcp_access = await client.post(
            "/v1/studio/platform-mcp/access",
            headers=headers,
        )

    schedule_id = schedule.json()["trigger"]["triggerId"]
    trigger_repository = cast(Any, container.triggers)._repository
    stored_schedule = await trigger_repository.get("tenant-a", schedule_id)
    due_at = datetime.now(UTC) - timedelta(seconds=1)
    assert await trigger_repository.advance_schedule(
        schedule_id,
        expected_next_fire_at=stored_schedule.next_fire_at,
        next_fire_at=due_at,
    )
    dispatch_counts = await asyncio.gather(
        container.triggers.dispatch_due(),
        container.triggers.dispatch_due(),
    )
    schedule_key = f"schedule:{due_at.isoformat()}"
    schedule_digest = hashlib.sha256(f"{schedule_id}:{schedule_key}".encode()).hexdigest()
    schedule_session_id = f"trigger_session_{schedule_digest[:32]}"
    schedule_runs = await container.runs.list_for_sessions("tenant-a", [schedule_session_id])

    assert promoted.status_code == 202
    assert card.status_code == 200
    assert card.json()["version"] == published.json()["version"]
    assert card.json()["securitySchemes"]["bearer"]["httpAuthSecurityScheme"]["scheme"] == "Bearer"
    assert card.json()["supportedInterfaces"][0]["protocolVersion"] == "1.0"
    assert card.json()["capabilities"]["streaming"] is True
    assert sent.status_code == 200
    assert task.json()["task"]["id"] == task_id
    assert cancelled.json()["task"]["status"]["state"] == "TASK_STATE_CANCELED"
    assert chatops.status_code == 202
    assert chatops.json()["runId"]
    assert schedule.status_code == 201
    assert schedule.json()["trigger"]["nextFireAt"]
    assert sorted(dispatch_counts) == [0, 1]
    assert len(schedule_runs) == 1
    assert mcp_access.status_code == 200
    assert mcp_access.json()["mutations_enabled"] is False
    assert mcp_access.json()["token"]


@pytest.mark.asyncio
async def test_a2a_1_0_projects_completed_tasks_context_streams_and_artifacts() -> None:
    application, _container = app_and_container(auto_execute=True)
    admin_headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "a2a-admin",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        created = await client.post(
            "/v1/studio/drafts",
            headers=admin_headers,
            json=draft_request("a2a-complete-agent"),
        )
        published = await client.post(
            f"/v1/studio/drafts/{created.json()['draftId']}/publish",
            headers=admin_headers,
        )
        promoted = await client.post(
            "/v1/studio/deployments/promote",
            headers=admin_headers,
            json={
                "agentName": "a2a-complete-agent",
                "agentVersion": published.json()["version"],
                "environment": "production",
                "expectedEnvironmentRevision": 0,
                "canaryPercent": 100,
                "imageDigest": "sha256:" + "d" * 64,
                "executionProfile": "isolated-default",
                "config": {},
                "idempotencyKey": "a2a-complete-release",
            },
        )
        trigger_response = await client.post(
            "/v1/studio/agents/a2a-complete-agent/triggers",
            headers=admin_headers,
            json={"name": "Partner A2A", "environment": "production", "kind": "a2a"},
        )
        trigger_id = trigger_response.json()["trigger"]["triggerId"]
        secret = trigger_response.json()["secret"]
        path = f"/a2a/agent-triggers/{trigger_id}"
        a2a_headers = {
            "Authorization": f"Bearer {secret}",
            "A2A-Version": "1.0",
            "Content-Type": "application/a2a+json",
        }

        card = await client.get(f"{path}/agent-card.json")
        cached_card = await client.get(
            f"{path}/agent-card.json",
            headers={"If-None-Match": card.headers["etag"]},
        )
        invalid_version = await client.post(
            f"{path}/message:send",
            headers={**a2a_headers, "A2A-Version": "0.3"},
            content=json.dumps(
                {
                    "message": {
                        "messageId": "invalid-version",
                        "role": "ROLE_USER",
                        "parts": [{"text": "hello"}],
                    }
                }
            ),
        )
        first = await client.post(
            f"{path}/message:send",
            headers=a2a_headers,
            content=json.dumps(
                {
                    "message": {
                        "messageId": "a2a-complete-1",
                        "role": "ROLE_USER",
                        "parts": [{"text": "生成合作方报告 [artifact]"}],
                    }
                }
            ),
        )
        first_task = first.json()["task"]
        file_artifact = next(
            item for item in first_task["artifacts"] if item["name"] == "result.txt"
        )
        artifact_download = await client.get(
            file_artifact["parts"][0]["url"],
            headers={"Authorization": f"Bearer {secret}"},
        )
        followup = await client.post(
            f"{path}/message:send",
            headers=a2a_headers,
            content=json.dumps(
                {
                    "message": {
                        "messageId": "a2a-complete-2",
                        "contextId": first_task["contextId"],
                        "role": "ROLE_USER",
                        "parts": [{"text": "继续补充结论"}],
                    }
                }
            ),
        )
        listed = await client.get(
            f"{path}/tasks",
            headers=a2a_headers,
            params={"pageSize": 1, "historyLength": 1, "includeArtifacts": "true"},
        )
        next_page = await client.get(
            f"{path}/tasks",
            headers=a2a_headers,
            params={"pageSize": 1, "pageToken": listed.json()["nextPageToken"]},
        )
        no_history = await client.get(
            f"{path}/tasks/{first_task['id']}",
            headers=a2a_headers,
            params={"historyLength": 0},
        )
        terminal_cancel = await client.post(
            f"{path}/tasks/{first_task['id']}:cancel",
            headers=a2a_headers,
        )
        terminal_subscribe = await client.post(
            f"{path}/tasks/{first_task['id']}:subscribe",
            headers=a2a_headers,
        )
        streamed = await client.post(
            f"{path}/message:stream",
            headers=a2a_headers,
            content=json.dumps(
                {
                    "message": {
                        "messageId": "a2a-stream-1",
                        "role": "ROLE_USER",
                        "parts": [{"text": "流式回答"}],
                    }
                }
            ),
        )

    assert promoted.status_code == 202, promoted.text
    assert card.status_code == 200
    assert card.json()["version"] == published.json()["version"]
    assert card.json()["skills"][0]["description"]
    assert cached_card.status_code == 304
    assert invalid_version.status_code == 400
    assert invalid_version.json()["error"]["details"][0]["reason"] == ("VERSION_NOT_SUPPORTED")
    assert first.status_code == 200, first.text
    assert first_task["status"]["state"] == "TASK_STATE_COMPLETED"
    assert first_task["status"]["timestamp"].endswith("Z")
    assert first_task["artifacts"][0]["parts"][0]["text"].startswith("Echo:")
    assert artifact_download.content == b"fake runtime artifact"
    assert followup.status_code == 200, followup.text
    assert followup.json()["task"]["contextId"] == first_task["contextId"]
    assert followup.json()["task"]["id"] != first_task["id"]
    assert listed.json()["totalSize"] == 2
    assert listed.json()["nextPageToken"]
    assert len(listed.json()["tasks"][0]["history"]) == 1
    assert len(next_page.json()["tasks"]) == 1
    assert "history" not in no_history.json()["task"]
    assert terminal_cancel.status_code == 400
    assert terminal_cancel.json()["error"]["details"][0]["reason"] == ("TASK_NOT_CANCELABLE")
    assert terminal_subscribe.status_code == 400
    assert terminal_subscribe.json()["error"]["details"][0]["reason"] == ("UNSUPPORTED_OPERATION")
    assert streamed.status_code == 200
    assert '"artifactUpdate"' in streamed.text
    assert '"lastChunk":true' in streamed.text
    assert '"statusUpdate"' in streamed.text
    assert "TASK_STATE_COMPLETED" in streamed.text


@pytest.mark.asyncio
async def test_eval_control_plane_runs_cases_persists_reports_and_exposes_gate() -> None:
    application, container = app_and_container()
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "evaluator-a",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        created = await client.post(
            "/v1/studio/drafts",
            headers=headers,
            json=draft_request("evaluated-agent"),
        )
        draft_id = created.json()["draftId"]
        dataset = await client.post(
            "/v1/studio/eval-datasets",
            headers=headers,
            json={
                "draftId": draft_id,
                "expectedRevision": 1,
                "name": "发布必测集",
                "required": True,
            },
        )
        published = await client.post(
            f"/v1/studio/drafts/{draft_id}/publish",
            headers=headers,
            json={"expectedRevision": 1},
        )
        started = await client.post(
            "/v1/studio/eval-runs",
            headers=headers,
            json={
                "datasetId": dataset.json()["datasetId"],
                "datasetVersion": dataset.json()["version"],
                "agentName": "evaluated-agent",
                "agentVersion": published.json()["version"],
                "idempotencyKey": "evaluated-agent-release-1",
            },
        )
        eval_run_id = started.json()["run"]["evalRunId"]
        await drain_eval(container, eval_run_id)
        finished = await client.get(f"/v1/studio/eval-runs/{eval_run_id}", headers=headers)
        listed = await client.get("/v1/studio/eval-runs", headers=headers)
        gate = await client.get(
            "/v1/studio/evaluation-gates/evaluated-agent/versions/0.1.0",
            headers=headers,
        )
        artifacts = finished.json()["run"]["artifacts"]
        report = await client.get(
            f"/v1/studio/eval-runs/{eval_run_id}/artifacts/{artifacts[0]['artifactId']}",
            headers=headers,
        )

    assert dataset.status_code == 201, dataset.text
    assert dataset.json()["version"] == 1
    assert len(dataset.json()["cases"]) == 3
    assert started.status_code == 202, started.text
    assert finished.status_code == 200
    assert finished.json()["run"]["status"] == EvalRunStatus.PASSED
    assert finished.json()["passedCases"] == 3
    assert len({item["sessionId"] for item in finished.json()["cases"]}) == 3
    assert listed.json()[0]["run"]["evalRunId"] == eval_run_id
    assert gate.json() == {
        "agentName": "evaluated-agent",
        "agentVersion": "0.1.0",
        "passed": True,
        "requiredDatasets": 1,
        "passedDatasets": 1,
        "missingDatasetIds": [],
    }
    assert {item["name"] for item in artifacts} == {"report.json", "junit.xml"}
    assert report.status_code == 200
    assert report.headers["content-disposition"] == 'attachment; filename="report.json"'
    assert report.json()["passed"] is True


@pytest.mark.asyncio
async def test_memory_auto_execute_drives_eval_and_child_run_queues() -> None:
    application, container = app_and_container(auto_execute=True)
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "evaluator-a",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        created = await client.post(
            "/v1/studio/drafts",
            headers=headers,
            json=draft_request("auto-eval-agent"),
        )
        dataset = await client.post(
            "/v1/studio/eval-datasets",
            headers=headers,
            json={
                "draftId": created.json()["draftId"],
                "expectedRevision": 1,
                "name": "自动评测集",
            },
        )
        await client.post(
            f"/v1/studio/drafts/{created.json()['draftId']}/publish",
            headers=headers,
            json={"expectedRevision": 1},
        )
        started = await client.post(
            "/v1/studio/eval-runs",
            headers=headers,
            json={
                "datasetId": dataset.json()["datasetId"],
                "datasetVersion": 1,
                "agentName": "auto-eval-agent",
                "agentVersion": "0.1.0",
                "idempotencyKey": "auto-eval-one",
            },
        )

    stored = await container.eval_run_repository.get("tenant-a", started.json()["run"]["evalRunId"])
    assert stored.status is EvalRunStatus.PASSED


@pytest.mark.asyncio
async def test_publish_is_idempotent_and_writes_secret_free_domain_audit() -> None:
    application, container = app_and_container()
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "owner-a",
    }
    prompt_sentinel = "PROMPT_BODY_MUST_NOT_ENTER_AUDIT"
    file_sentinel = "PRIVATE_SKILL_FILE_MUST_NOT_ENTER_AUDIT"
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        created = await client.post("/v1/studio/drafts", headers=headers, json=draft_request())
        spec = created.json()["spec"]
        spec["systemPrompt"] += f"\n{prompt_sentinel}\n"
        spec["skills"] = [
            {
                "name": "audit-evidence",
                "description": "Keep private evidence out of audit records.",
                "instructions": "Use the private reference without copying it into audit logs.",
                "files": [{"path": "references/private.md", "content": file_sentinel}],
            }
        ]
        replaced = await client.put(
            f"/v1/studio/drafts/{created.json()['draftId']}",
            headers=headers,
            json={"expectedRevision": 1, "spec": spec},
        )
        first = await client.post(
            f"/v1/studio/drafts/{created.json()['draftId']}/publish",
            headers=headers,
            json={"expectedRevision": 2},
        )
        repeated = await client.post(
            f"/v1/studio/drafts/{created.json()['draftId']}/publish",
            headers=headers,
            json={"expectedRevision": 3},
        )
        stored = await client.get(f"/v1/studio/drafts/{created.json()['draftId']}", headers=headers)

    assert replaced.status_code == 200
    assert first.status_code == 200
    assert repeated.status_code == 200
    assert repeated.json() == first.json()
    assert stored.json()["revision"] == 3
    domain_audits = [
        entry
        for entry in await container.audit.list_for_tenant("tenant-a", limit=20)
        if entry.action == "studio.publish"
    ]
    assert len(domain_audits) == 2
    assert {entry.details["idempotent"] for entry in domain_audits} == {
        False,
        True,
    }
    audit_json = json.dumps([entry.model_dump(mode="json") for entry in domain_audits])
    assert prompt_sentinel not in audit_json
    assert file_sentinel not in audit_json
    assert "systemPrompt" not in audit_json
    assert "files" not in audit_json


@pytest.mark.asyncio
async def test_changed_published_content_auto_increments_patch_version() -> None:
    application, container = app_and_container()
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "owner-a",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        created = await client.post("/v1/studio/drafts", headers=headers, json=draft_request())
        draft_id = created.json()["draftId"]
        first = await client.post(
            f"/v1/studio/drafts/{draft_id}/publish",
            headers=headers,
            json={"expectedRevision": 1},
        )
        changed_spec = (await client.get(f"/v1/studio/drafts/{draft_id}", headers=headers)).json()[
            "spec"
        ]
        changed_spec["description"] = "版本号未变化，但内容已经变化。"
        changed = await client.put(
            f"/v1/studio/drafts/{draft_id}",
            headers=headers,
            json={"expectedRevision": 2, "spec": changed_spec},
        )
        republished = await client.post(
            f"/v1/studio/drafts/{draft_id}/publish",
            headers=headers,
            json={"expectedRevision": 3},
        )

    assert first.status_code == 200
    assert changed.status_code == 200
    assert changed.json()["spec"]["version"] == "0.1.1"
    assert republished.status_code == 200
    assert republished.json()["version"] == "0.1.1"
    audits = await container.audit.list_for_tenant("tenant-a", limit=20)
    assert not any(
        entry.action == "studio.publish" and entry.outcome == "denied" for entry in audits
    )


@pytest.mark.asyncio
async def test_unpublished_subagent_blocks_validation_and_publish_api() -> None:
    application = app()
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "owner-a",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        created = await client.post(
            "/v1/studio/drafts",
            headers=headers,
            json={**draft_request("lead-agent"), "template": "orchestrator"},
        )
        draft_id = created.json()["draftId"]
        spec = created.json()["spec"]
        spec["builtinTools"].append("Task")
        spec["subagents"] = [
            {
                "alias": "reviewer",
                "ref": "unpublished-reviewer@1.0.0",
                "responsibility": "复核主任务结果",
            }
        ]
        updated = await client.put(
            f"/v1/studio/drafts/{draft_id}",
            headers=headers,
            json={"expectedRevision": 1, "spec": spec},
        )
        assert updated.status_code == 200, updated.text
        validation = await client.post(f"/v1/studio/drafts/{draft_id}/validate", headers=headers)
        published = await client.post(
            f"/v1/studio/drafts/{draft_id}/publish",
            headers=headers,
            json={"expectedRevision": 2},
        )

    assert validation.status_code == 200
    assert validation.json()["ready"] is False
    assert {issue["code"] for issue in validation.json()["issues"]} >= {"subagent_not_published"}
    assert published.status_code == 422
    assert published.json()["error"]["code"] == "draft_not_ready"
    assert {issue["code"] for issue in published.json()["error"]["issues"]} >= {
        "subagent_not_published"
    }


@pytest.mark.asyncio
async def test_preview_api_is_idempotent_stale_cancellable_and_never_publishes() -> None:
    application, container = app_and_container(auto_execute=True)
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "previewer-a",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        created = await client.post(
            "/v1/studio/drafts", headers=headers, json=draft_request("preview-agent")
        )
        draft_id = created.json()["draftId"]
        preview_request = {
            "draftId": draft_id,
            "expectedRevision": 1,
            "idempotencyKey": "preview-agent-r1",
            "ttlSeconds": 600,
        }
        first = await client.post("/v1/studio/previews", headers=headers, json=preview_request)
        repeated = await client.post("/v1/studio/previews", headers=headers, json=preview_request)
        listed = await client.get("/v1/studio/previews", headers=headers)
        current = await client.get(
            f"/v1/studio/previews/{first.json()['previewId']}", headers=headers
        )
        preflight_events = await client.get(
            f"/v1/studio/previews/{first.json()['previewId']}/events",
            headers=headers,
        )

        changed_spec = created.json()["spec"]
        changed_spec["description"] = "Draft changed after Preview creation."
        changed = await client.put(
            f"/v1/studio/drafts/{draft_id}",
            headers=headers,
            json={"expectedRevision": 1, "spec": changed_spec},
        )
        stale = await client.get(
            f"/v1/studio/previews/{first.json()['previewId']}", headers=headers
        )
        cancelling = await client.post(
            f"/v1/studio/previews/{first.json()['previewId']}/cancel",
            headers=headers,
        )
        cancelled = await client.get(
            f"/v1/studio/previews/{first.json()['previewId']}", headers=headers
        )

    assert first.status_code == 202
    assert first.json()["identityKind"] == "test"
    assert first.json()["environment"] == "preview"
    assert repeated.status_code == 202
    assert repeated.json()["previewId"] == first.json()["previewId"]
    assert len(listed.json()) == 1
    assert current.json()["status"] == "ready"
    assert current.json()["preflightResult"]["schemaVersion"] == "harness.preflight/v1"
    assert current.json()["preflightResult"]["status"] == "passed"
    assert {check["stage"] for check in current.json()["preflightResult"]["checks"]} == {
        "bundle",
        "sandbox_provision",
        "sandbox_prepare",
        "model",
        "mcp",
        "approval",
        "workspace_artifact",
        "cleanup",
    }
    assert preflight_events.status_code == 200
    assert len(preflight_events.json()) == 16
    assert changed.status_code == 200
    assert stale.json()["stale"] is True
    assert stale.json()["staleReason"] == "draft_revision_changed"
    assert cancelling.json()["status"] == "cancelling"
    assert cancelled.json()["status"] == "cancelled"
    registry = cast(InMemoryAgentRegistry, vars(container.agents)["_registry"])
    with pytest.raises(NotFoundError):
        await registry.get("tenant-a", "owner", "preview-agent", "0.1.0")


@pytest.mark.asyncio
async def test_jwt_identity_ignores_spoofed_tenant_and_user_headers() -> None:
    async with AsyncClient(transport=ASGITransport(app=app()), base_url="http://test") as client:
        owner = await register(client, "owner@example.com")
        body_spoofed = await client.post(
            "/v1/studio/drafts",
            headers={"Authorization": f"Bearer {owner['access_token']}"},
            json={
                **draft_request(),
                "tenantId": "tenant-evil",
                "createdBy": "user-evil",
            },
        )
        created = await client.post(
            "/v1/studio/drafts",
            headers={
                "Authorization": f"Bearer {owner['access_token']}",
                "X-Tenant-ID": "tenant-evil",
                "X-User-ID": "user-evil",
            },
            json=draft_request(),
        )

    assert body_spoofed.status_code == 422
    assert created.status_code == 201
    assert created.json()["tenantId"] == owner["membership"]["tenant_id"]
    assert created.json()["createdBy"] == owner["user"]["user_id"]
    assert created.json()["tenantId"] != "tenant-evil"
    assert created.json()["createdBy"] != "user-evil"


@pytest.mark.asyncio
async def test_member_can_write_validate_and_publish_personal_agent() -> None:
    async with AsyncClient(transport=ASGITransport(app=app()), base_url="http://test") as client:
        await register(client, "owner@example.com")
        member = await register(client, "member@example.com")
        headers = {"Authorization": f"Bearer {member['access_token']}"}
        created = await client.post("/v1/studio/drafts", headers=headers, json=draft_request())
        draft_id = created.json()["draftId"]
        validation = await client.post(f"/v1/studio/drafts/{draft_id}/validate", headers=headers)
        published = await client.post(f"/v1/studio/drafts/{draft_id}/publish", headers=headers)

    assert member["membership"]["role"] == "member"
    assert created.status_code == 201
    assert validation.status_code == 200
    assert published.status_code == 200, published.text


@pytest.mark.asyncio
async def test_quota_usage_is_readable_but_only_admin_roles_can_change_policy() -> None:
    application, container = app_and_container()
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        owner = await register(client, "quota-owner@example.com")
        member = await register(client, "quota-member@example.com")
        viewer = await register(client, "quota-viewer@example.com")
        repository = cast(InMemoryAuthRepository, vars(container.auth)["_repository"])
        viewer_membership = Membership(
            tenant_id=viewer["membership"]["tenant_id"],
            user_id=viewer["user"]["user_id"],
            role="viewer",
            created_at=repository.memberships[
                (viewer["membership"]["tenant_id"], viewer["user"]["user_id"])
            ].created_at,
        )
        repository.memberships[(viewer_membership.tenant_id, viewer_membership.user_id)] = (
            viewer_membership
        )
        viewer_login = await client.post(
            "/v1/auth/login",
            json={"email": "quota-viewer@example.com", "password": "SecurePass123"},
        )
        owner_headers = {"Authorization": f"Bearer {owner['access_token']}"}
        member_headers = {"Authorization": f"Bearer {member['access_token']}"}
        viewer_headers = {"Authorization": f"Bearer {viewer_login.json()['access_token']}"}

        initial = await client.get("/v1/studio/quotas", headers=viewer_headers)
        member_write = await client.put(
            "/v1/studio/quotas/tenant-default",
            headers=member_headers,
            json={"expectedRevision": 0, "scope": {}, "limits": {"concurrent_runs": 3}},
        )
        viewer_write = await client.put(
            "/v1/studio/quotas/tenant-default",
            headers=viewer_headers,
            json={"expectedRevision": 0, "scope": {}, "limits": {"concurrent_runs": 3}},
        )
        changed = await client.put(
            "/v1/studio/quotas/tenant-default",
            headers=owner_headers,
            json={"expectedRevision": 0, "scope": {}, "limits": {"concurrent_runs": 3}},
        )
        current = await client.get("/v1/studio/quotas", headers=member_headers)

    assert initial.status_code == 200
    assert initial.json()["policies"][0]["revision"] == 0
    assert member_write.status_code == 403
    assert viewer_write.status_code == 403
    assert changed.status_code == 200
    assert changed.json()["revision"] == 1
    assert current.status_code == 200
    assert current.json()["policies"][0]["limits"]["concurrent_runs"] == 3
    audits = await container.audit.list_for_tenant(owner["membership"]["tenant_id"], limit=20)
    assert any(
        entry.action == "quota.policy.replace"
        and entry.resource_id == "tenant-default"
        and entry.user_id == owner["user"]["user_id"]
        for entry in audits
    )


@pytest.mark.asyncio
async def test_preview_quota_rejection_does_not_create_a_half_preview() -> None:
    container = replace(
        build_memory_container(settings=Settings(quota_enforcement_enabled=True)),
        environment="production",
        api_bearer_token=SecretStr(SERVICE_TOKEN),
    )
    application = create_app(container)
    await container.quotas.replace_policy(
        tenant_id="tenant-a",
        user_id="owner-a",
        policy_id="tenant-default",
        request=ReplaceQuotaPolicyRequest(
            expectedRevision=0,
            limits={QuotaResource.ACTIVE_PREVIEWS: 1},
        ),
    )
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "owner-a",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        draft = await client.post(
            "/v1/studio/drafts", headers=headers, json=draft_request("preview-quota")
        )
        first = await client.post(
            "/v1/studio/previews",
            headers=headers,
            json={
                "draftId": draft.json()["draftId"],
                "expectedRevision": 1,
                "idempotencyKey": "preview-quota-one",
                "ttlSeconds": 600,
            },
        )
        rejected = await client.post(
            "/v1/studio/previews",
            headers=headers,
            json={
                "draftId": draft.json()["draftId"],
                "expectedRevision": 1,
                "idempotencyKey": "preview-quota-two",
                "ttlSeconds": 600,
            },
        )
        listed = await client.get("/v1/studio/previews", headers=headers)

    assert first.status_code == 202
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "quota_exceeded"
    assert "active_previews" in rejected.json()["error"]["message"]
    assert [item["previewId"] for item in listed.json()] == [first.json()["previewId"]]


@pytest.mark.asyncio
async def test_catalog_is_admin_managed_secret_free_and_drives_live_validation() -> None:
    owner_headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "owner-a",
    }
    async with AsyncClient(transport=ASGITransport(app=app()), base_url="http://test") as client:
        await register(client, "owner@example.com")
        member = await register(client, "member@example.com")
        member_headers = {"Authorization": f"Bearer {member['access_token']}"}
        catalog = await client.get("/v1/studio/catalog", headers=owner_headers)
        created = await client.post(
            "/v1/studio/drafts", headers=owner_headers, json=draft_request()
        )
        draft_id = created.json()["draftId"]

        rejected_secret = catalog.json()["catalog"]
        rejected_secret["modelRoutes"][0]["apiKey"] = "must-never-be-stored"
        secret_response = await client.put(
            "/v1/studio/catalog",
            headers=owner_headers,
            json={"expectedRevision": 1, "catalog": rejected_secret},
        )
        member_response = await client.put(
            "/v1/studio/catalog",
            headers=member_headers,
            json={"expectedRevision": 1, "catalog": catalog.json()["catalog"]},
        )
        member_registration = await client.put(
            "/v1/studio/catalog/policy/member-policy",
            headers=member_headers,
            json={
                "expectedRevision": 1,
                "resource": {
                    "policyId": "member-policy",
                    "label": "越权策略",
                    "description": "普通 Builder 不得创建。",
                    "risk": "low",
                },
            },
        )
        disabled = await client.delete(
            "/v1/studio/catalog/modelRoute/deepseek-v4-pro",
            headers=owner_headers,
            params={"expected_revision": 1},
        )
        validation = await client.post(
            f"/v1/studio/drafts/{draft_id}/validate", headers=owner_headers
        )
        current = await client.get("/v1/studio/catalog", headers=owner_headers)
        member_catalog = await client.get("/v1/studio/catalog", headers=member_headers)

    assert catalog.status_code == 200
    assert catalog.json()["revision"] == 1
    assert secret_response.status_code == 422
    assert secret_response.json()["error"]["code"] == "request_invalid"
    assert "must-never-be-stored" not in secret_response.text
    assert "must-never-be-stored" not in current.text
    assert member_response.status_code == 403
    assert member_response.json()["error"]["code"] == "permission_denied"
    assert member_registration.status_code == 403
    assert member_registration.json()["error"]["code"] == "permission_denied"
    assert disabled.status_code == 200
    assert disabled.json()["record"]["revision"] == 2
    assert disabled.json()["impact"]["draftIds"] == [draft_id]
    assert validation.status_code == 200
    assert validation.json()["ready"] is False
    assert {issue["code"] for issue in validation.json()["issues"]} >= {"model_route_disabled"}
    assert current.status_code == 200
    assert current.json()["revision"] == 2
    assert member_catalog.status_code == 200
    assert member_catalog.json()["tenantId"] == member["membership"]["tenant_id"]


@pytest.mark.asyncio
async def test_model_management_is_admin_only_and_never_returns_api_keys() -> None:
    owner_headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "owner-a",
    }
    async with AsyncClient(transport=ASGITransport(app=app()), base_url="http://test") as client:
        await register(client, "model-owner@example.com")
        member = await register(client, "model-member@example.com")
        member_headers = {"Authorization": f"Bearer {member['access_token']}"}

        initial = await client.get("/v1/studio/models", headers=owner_headers)
        configured = await client.put(
            "/v1/studio/models/vision-primary",
            headers=owner_headers,
            json={
                "expectedRevision": initial.json()["revision"],
                "label": "视觉主模型",
                "modelType": "vision",
                "provider": "Example AI",
                "model": "vision-1",
                "baseUrl": "https://models.example.test/v1",
                "apiFormat": "openai_compatible",
                "authScheme": "bearer",
                "apiKey": "api-key-must-never-be-returned",
                "enabled": True,
            },
        )
        video = await client.put(
            "/v1/studio/models/minimax-h3-video",
            headers=owner_headers,
            json={
                "expectedRevision": configured.json()["revision"],
                "label": "MiniMax H3 视频",
                "modelType": "video_generation",
                "provider": "MiniMax",
                "model": "/model",
                "baseUrl": "http://172.20.109.229:18000/v1",
                "apiFormat": "openai_videos",
                "authScheme": "none",
                "apiKey": None,
                "enabled": True,
            },
        )
        denied = await client.get("/v1/studio/models", headers=member_headers)
        runtime_catalog = await client.get("/v1/studio/catalog", headers=owner_headers)

    assert initial.status_code == 200
    assert configured.status_code == 200
    assert "api-key-must-never-be-returned" not in configured.text
    model = next(
        item for item in configured.json()["models"] if item["routeId"] == "vision-primary"
    )
    assert model["credentialConfigured"] is True
    assert video.status_code == 200
    video_model = next(
        item for item in video.json()["models"] if item["routeId"] == "minimax-h3-video"
    )
    assert video_model["modelType"] == "video_generation"
    assert video_model["apiFormat"] == "openai_videos"
    assert video_model["authScheme"] == "none"
    assert video_model["credentialConfigured"] is True
    assert denied.status_code == 403
    public_route = next(
        item
        for item in runtime_catalog.json()["catalog"]["modelRoutes"]
        if item["routeId"] == "vision-primary"
    )
    assert public_route["baseUrl"] is None


@pytest.mark.asyncio
async def test_video_generation_forwards_user_owned_single_and_multiple_images() -> None:
    application, container = app_and_container()
    captured: list[httpx.Request] = []

    def provider(incoming: httpx.Request) -> httpx.Response:
        captured.append(incoming)
        if incoming.method == "POST":
            return httpx.Response(
                200,
                json={"id": "provider-video-1", "status": "queued", "progress": 0},
            )
        if incoming.url.path.endswith("/content"):
            return httpx.Response(
                200,
                content=b"\x00\x00\x00\x18ftypmp42video-bytes",
                headers={"content-type": "video/mp4"},
            )
        if incoming.method == "DELETE":
            return httpx.Response(
                200,
                json={"id": "provider-video-1", "deleted": True},
            )
        return httpx.Response(
            200,
            json={"id": "provider-video-1", "status": "completed", "progress": 100},
        )

    owner_headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-video",
        "X-User-ID": "video-owner",
    }
    other_headers = {
        **owner_headers,
        "X-User-ID": "other-user",
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as provider_client:
        container.model_configurations._http_client = provider_client
        async with AsyncClient(
            transport=ASGITransport(app=application), base_url="http://test"
        ) as client:
            initial = await client.get("/v1/studio/models", headers=owner_headers)
            configured = await client.put(
                "/v1/studio/models/minimax-h3-video",
                headers=owner_headers,
                json={
                    "expectedRevision": initial.json()["revision"],
                    "label": "MiniMax H3 视频",
                    "modelType": "video_generation",
                    "provider": "MiniMax",
                    "model": "/model",
                    "baseUrl": "http://172.20.109.229:18000/v1",
                    "apiFormat": "openai_videos",
                    "authScheme": "none",
                    "apiKey": None,
                    "enabled": True,
                },
            )
            uploads = [
                await client.post(
                    "/v1/input-artifacts",
                    headers=owner_headers,
                    files={"file": (name, content, media_type)},
                )
                for name, content, media_type in (
                    ("first.png", image_bytes(), "image/png"),
                    ("second.jpg", image_bytes(format="JPEG"), "image/jpeg"),
                )
            ]
            artifact_ids = [upload.json()["input_artifact_id"] for upload in uploads]
            denied = await client.post(
                "/v1/studio/models/minimax-h3-video/videos",
                headers=other_headers,
                json={"prompt": "无权读取", "inputArtifactIds": artifact_ids[:1]},
            )
            generated = await client.post(
                "/v1/studio/models/minimax-h3-video/videos",
                headers=owner_headers,
                json={
                    "prompt": "组合两张参考图",
                    "mode": "ref2va",
                    "seconds": 11,
                    "negativePrompt": "画面抖动、文字水印",
                    "inputArtifactIds": artifact_ids,
                },
            )
            job_id = generated.json()["jobId"]
            hidden = await client.get(
                f"/v1/studio/models/minimax-h3-video/videos/{job_id}",
                headers=other_headers,
            )
            completed = await client.get(
                f"/v1/studio/models/minimax-h3-video/videos/{job_id}",
                headers=owner_headers,
            )
            content = await client.get(
                f"/v1/studio/models/minimax-h3-video/videos/{job_id}/content",
                headers=owner_headers,
            )
            cancelled = await client.delete(
                f"/v1/studio/models/minimax-h3-video/videos/{job_id}",
                headers=owner_headers,
            )

    assert configured.status_code == 200
    assert all(upload.status_code == 201 for upload in uploads)
    assert denied.status_code == 404
    assert generated.status_code == 200
    assert generated.json()["status"] == "queued"
    assert hidden.status_code == 404
    assert completed.json()["status"] == "completed"
    assert content.headers["content-type"] == "video/mp4"
    assert cancelled.json()["status"] == "cancelled"
    assert len(captured) == 4
    assert captured[0].content.count(b'name="input_references"') == 2
    assert b"\r\n11\r\n" in captured[0].content
    assert b'name="negative_prompt"' in captured[0].content
    assert b'{"task":"ref2va"}' in captured[0].content
    assert image_bytes() in captured[0].content
    assert image_bytes(format="JPEG") in captured[0].content
    assert captured[1].url.path == "/v1/videos/provider-video-1"
    assert captured[2].url.path == "/v1/videos/provider-video-1/content"
    assert captured[3].method == "DELETE"


@pytest.mark.asyncio
async def test_model_management_can_permanently_delete_model_and_secret() -> None:
    owner_headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "owner-a",
    }
    api, container = app_and_container()
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as client:
        initial = await client.get("/v1/studio/models", headers=owner_headers)
        configured = await client.put(
            "/v1/studio/models/minimax-h3",
            headers=owner_headers,
            json={
                "expectedRevision": initial.json()["revision"],
                "label": "MiniMax H3",
                "modelType": "chat",
                "provider": "MiniMax",
                "model": "MiniMax-H3",
                "baseUrl": "https://models.example.test/v1",
                "apiFormat": "anthropic_compatible",
                "authScheme": "x-api-key",
                "apiKey": "api-key-must-be-deleted",
                "enabled": True,
            },
        )
        deleted = await client.delete(
            "/v1/studio/models/minimax-h3/permanent",
            headers=owner_headers,
            params={"expectedRevision": configured.json()["revision"]},
        )

    assert configured.status_code == 200
    assert deleted.status_code == 200
    assert "minimax-h3" not in {item["routeId"] for item in deleted.json()["models"]}
    assert "api-key-must-be-deleted" not in deleted.text
    assert (
        await container.mcp_credentials.repository.get(
            "tenant-a", "tenant:model-control-plane", "minimax-h3"
        )
        is None
    )


@pytest.mark.asyncio
async def test_each_studio_writer_can_discover_mcp_tools() -> None:
    class Connector:
        async def discover(
            self,
            endpoint_url: str,
            *,
            headers: Mapping[str, str],
            timeout_seconds: float,
        ) -> DiscoveredServer:
            assert endpoint_url == "https://mcp.example.com/mcp"
            assert headers == {}
            assert timeout_seconds == 12
            return DiscoveredServer(
                title="Company MCP",
                version="1.0.0",
                tools=(
                    ("search", "Search", "Search documents"),
                    ("open", "Open", "Open a document"),
                ),
            )

    async def resolve_public(_host: str, _port: int) -> tuple[str, ...]:
        return ("1.1.1.1",)

    application, container = app_and_container()
    object.__setattr__(
        container,
        "mcp_discovery",
        McpDiscoveryService(
            connector=Connector(),
            host_resolver=resolve_public,
        ),
    )
    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        owner = await register(client, "mcp-owner@example.com")
        member = await register(client, "mcp-member@example.com")
        body = {
            "reference": "company-search",
            "serverName": "company",
            "endpointUrl": "https://mcp.example.com/mcp",
            "networkAccess": "external",
            "authMode": "none",
            "authKey": "authorization",
        }
        discovered = await client.post(
            "/v1/studio/mcp/discover",
            headers={"Authorization": f"Bearer {owner['access_token']}"},
            json=body,
        )
        rejected = await client.post(
            "/v1/studio/mcp/discover",
            headers={"Authorization": f"Bearer {member['access_token']}"},
            json=body,
        )
        member_credential = await client.put(
            "/v1/studio/mcp/company-search/credentials",
            headers={"Authorization": f"Bearer {member['access_token']}"},
            json={"authKey": "authorization", "value": "member-secret"},
        )

    assert discovered.status_code == 200
    assert discovered.json()["transport"] == "http"
    assert [tool["canonicalName"] for tool in discovered.json()["tools"]] == [
        "mcp__company__search",
        "mcp__company__open",
    ]
    assert rejected.status_code == 200
    assert member_credential.status_code == 200


@pytest.mark.asyncio
async def test_same_tenant_users_have_private_mcp_catalogs() -> None:
    application = app()
    alice = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-personal-mcp",
        "X-User-ID": "alice",
    }
    bob = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-personal-mcp",
        "X-User-ID": "bob",
    }

    def resource(label: str) -> dict[str, object]:
        return {
            "reference": "company-search",
            "serverName": "company-search",
            "label": label,
            "description": "Personal company search.",
            "endpointUrl": "https://mcp.example.com/mcp",
            "transport": "http",
            "tools": ["mcp__company_search__search"],
            "risk": "medium",
            "networkAccess": "external",
            "sendsUserData": True,
            "readOnly": True,
            "credentialManaged": True,
            "executionLocation": "external-mcp",
            "preflightRequired": True,
            "authMode": "none",
            "authKey": "authorization",
            "enabled": True,
        }

    async with AsyncClient(
        transport=ASGITransport(app=application),
        base_url="http://test",
    ) as client:
        initial = await client.get("/v1/studio/catalog", headers=alice)
        alice_saved = await client.put(
            "/v1/studio/catalog/mcp/company-search",
            headers=alice,
            json={
                "expectedRevision": initial.json()["revision"],
                "resource": resource("Alice search"),
                "allowedExecutionProfileIds": ["local-development"],
            },
        )
        hidden_from_bob = await client.get("/v1/studio/catalog", headers=bob)
        bob_saved = await client.put(
            "/v1/studio/catalog/mcp/company-search",
            headers=bob,
            json={
                "expectedRevision": alice_saved.json()["record"]["revision"],
                "resource": resource("Bob search"),
                "allowedExecutionProfileIds": ["local-development"],
            },
        )
        alice_after = await client.get("/v1/studio/catalog", headers=alice)

    assert alice_saved.status_code == 200, alice_saved.text
    assert "company-search" not in {
        item["reference"] for item in hidden_from_bob.json()["catalog"]["mcpServers"]
    }
    bob_item = next(
        item
        for item in bob_saved.json()["record"]["catalog"]["mcpServers"]
        if item["reference"] == "company-search"
    )
    alice_item = next(
        item
        for item in alice_after.json()["catalog"]["mcpServers"]
        if item["reference"] == "company-search"
    )
    assert bob_item["label"] == "Bob search"
    assert bob_item["ownerUserId"] == "bob"
    assert alice_item["label"] == "Alice search"
    assert alice_item["ownerUserId"] == "alice"


@pytest.mark.asyncio
async def test_published_agent_version_is_immutable_after_catalog_change() -> None:
    application, container = app_and_container()
    owner_headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "owner-a",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        created = await client.post(
            "/v1/studio/drafts", headers=owner_headers, json=draft_request()
        )
        published = await client.post(
            f"/v1/studio/drafts/{created.json()['draftId']}/publish",
            headers=owner_headers,
        )
        registry = cast(InMemoryAgentRegistry, vars(container.agents)["_registry"])
        stored_before = await registry.get("tenant-a", "owner-a", "policy-researcher", "0.1.0")
        disabled = await client.delete(
            "/v1/studio/catalog/modelRoute/deepseek-v4-flash",
            headers=owner_headers,
            params={"expected_revision": 1},
        )
        stored_after = await registry.get("tenant-a", "owner-a", "policy-researcher", "0.1.0")

    assert published.status_code == 200
    assert disabled.status_code == 200
    assert stored_after == stored_before
    assert stored_after.manifest_hash == published.json()["manifest_hash"]


@pytest.mark.asyncio
async def test_same_tenant_users_have_private_agent_namespaces() -> None:
    application, container = app_and_container()
    alice = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "alice",
    }
    bob = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "bob",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        alice_draft = await client.post(
            "/v1/studio/drafts", headers=alice, json=draft_request("shared-name")
        )
        bob_draft = await client.post(
            "/v1/studio/drafts", headers=bob, json=draft_request("shared-name")
        )
        alice_id = alice_draft.json()["draftId"]
        bob_id = bob_draft.json()["draftId"]

        hidden_from_bob = await client.get(f"/v1/studio/drafts/{alice_id}", headers=bob)
        hidden_from_alice = await client.get(f"/v1/studio/drafts/{bob_id}", headers=alice)
        alice_publish = await client.post(f"/v1/studio/drafts/{alice_id}/publish", headers=alice)
        bob_publish = await client.post(f"/v1/studio/drafts/{bob_id}/publish", headers=bob)
        alice_catalog = await client.get("/v1/agents", headers=alice)
        bob_catalog = await client.get("/v1/agents", headers=bob)

    assert alice_draft.status_code == 201
    assert bob_draft.status_code == 201
    assert hidden_from_bob.status_code == 404
    assert hidden_from_alice.status_code == 404
    assert alice_publish.status_code == 200
    assert bob_publish.status_code == 200
    assert [item["name"] for item in alice_catalog.json()] == ["shared-name"]
    assert [item["name"] for item in bob_catalog.json()] == ["shared-name"]
    registry = cast(InMemoryAgentRegistry, vars(container.agents)["_registry"])
    assert (
        await registry.get("tenant-a", "alice", "shared-name", "0.1.0")
    ).owner_user_id == "alice"
    assert (await registry.get("tenant-a", "bob", "shared-name", "0.1.0")).owner_user_id == "bob"


@pytest.mark.asyncio
async def test_admin_can_create_update_and_disable_catalog_registration() -> None:
    owner_headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "owner-a",
    }
    resource = {
        "policyId": "regulated-review",
        "label": "受监管审查",
        "description": "对受监管材料使用更严格的审批边界。",
        "risk": "high",
    }
    async with AsyncClient(transport=ASGITransport(app=app()), base_url="http://test") as client:
        created = await client.put(
            "/v1/studio/catalog/policy/regulated-review",
            headers=owner_headers,
            json={"expectedRevision": 1, "resource": resource},
        )
        created_policy = next(
            item
            for item in created.json()["record"]["catalog"]["policies"]
            if item["policyId"] == "regulated-review"
        )
        updated_resource = {**created_policy, "label": "受监管材料审查"}
        updated = await client.put(
            "/v1/studio/catalog/policy/regulated-review",
            headers=owner_headers,
            json={"expectedRevision": 2, "resource": updated_resource},
        )
        disabled = await client.delete(
            "/v1/studio/catalog/policy/regulated-review",
            headers=owner_headers,
            params={"expected_revision": 3},
        )

    assert created.status_code == 200
    assert created.json()["record"]["revision"] == 2
    assert created_policy["version"] == 1
    assert updated.status_code == 200
    updated_policy = next(
        item
        for item in updated.json()["record"]["catalog"]["policies"]
        if item["policyId"] == "regulated-review"
    )
    assert updated_policy["version"] == 2
    assert updated_policy["label"] == "受监管材料审查"
    assert disabled.status_code == 200
    disabled_policy = next(
        item
        for item in disabled.json()["record"]["catalog"]["policies"]
        if item["policyId"] == "regulated-review"
    )
    assert disabled_policy["enabled"] is False
    assert disabled_policy["version"] == 3


@pytest.mark.asyncio
async def test_studio_contract_hides_tenants_and_reports_conflict_and_invalid_bundle() -> None:
    tenant_a = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-a",
        "X-User-ID": "builder-a",
    }
    tenant_b = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "tenant-b",
        "X-User-ID": "builder-b",
    }
    async with AsyncClient(transport=ASGITransport(app=app()), base_url="http://test") as client:
        created = await client.post("/v1/studio/drafts", headers=tenant_a, json=draft_request())
        draft_id = created.json()["draftId"]
        hidden = await client.get(f"/v1/studio/drafts/{draft_id}", headers=tenant_b)

        first_spec = created.json()["spec"]
        first_spec["description"] = "第一次保存。"
        first_update = await client.put(
            f"/v1/studio/drafts/{draft_id}",
            headers=tenant_a,
            json={"expectedRevision": 1, "spec": first_spec},
        )
        stale = await client.put(
            f"/v1/studio/drafts/{draft_id}",
            headers=tenant_a,
            json={"expectedRevision": 1, "spec": first_spec},
        )

        invalid_spec = first_update.json()["spec"]
        invalid_spec["builtinTools"] = [*invalid_spec["builtinTools"], "UnknownTool"]
        invalid_update = await client.put(
            f"/v1/studio/drafts/{draft_id}",
            headers=tenant_a,
            json={"expectedRevision": 2, "spec": invalid_spec},
        )
        invalid_bundle = await client.get(f"/v1/studio/drafts/{draft_id}/bundle", headers=tenant_a)

    assert created.status_code == 201
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "not_found"
    assert first_update.status_code == 200
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "draft_conflict"
    assert invalid_update.status_code == 200
    assert invalid_bundle.status_code == 422
    assert invalid_bundle.json()["error"]["code"] == "draft_not_ready"


def test_studio_routes_are_exposed_once_in_openapi() -> None:
    schema = app().openapi()
    expected = {
        "/v1/studio/capabilities",
        "/v1/studio/drafts",
        "/v1/studio/drafts/from-task",
        "/v1/studio/drafts/import",
        "/v1/studio/drafts/{draft_id}",
        "/v1/studio/drafts/{draft_id}/validate",
        "/v1/studio/drafts/{draft_id}/builder-patch",
        "/v1/studio/drafts/{draft_id}/try-runs",
        "/v1/studio/drafts/{draft_id}/try-runs/{run_id}",
        "/v1/studio/drafts/{draft_id}/solidify",
        "/v1/studio/drafts/{draft_id}/bundle",
        "/v1/studio/drafts/{draft_id}/publish",
        "/v1/studio/previews",
        "/v1/studio/previews/{preview_id}",
        "/v1/studio/previews/{preview_id}/events",
        "/v1/studio/previews/{preview_id}/cancel",
    }

    assert expected <= set(schema["paths"])
    assert schema["paths"]["/v1/studio/drafts"]["post"]["responses"].keys() >= {
        "201",
        "422",
    }


@pytest.mark.asyncio
async def test_preview_continuation_reuses_owned_session_and_preserves_raw_prompts() -> None:
    application, _ = app_and_container(auto_execute=True)
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "preview-multi",
        "X-User-ID": "builder-a",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        draft = (
            await client.post(
                "/v1/studio/drafts", headers=headers, json=draft_request("multi-preview")
            )
        ).json()
        url = f"/v1/studio/drafts/{draft['draftId']}/try-runs"
        first = await client.post(
            url,
            headers=headers,
            json={"expectedRevision": 1, "prompt": "记住项目：北斗", "idempotencyKey": "first"},
        )
        second_body = {
            "expectedRevision": 1,
            "prompt": "它叫什么？",
            "idempotencyKey": "second",
            "continueFromRunId": first.json()["run"]["run_id"],
        }
        second = await client.post(url, headers=headers, json=second_body)
        assert second.status_code == 202, second.text
        assert second.json()["run"]["session_id"] == first.json()["run"]["session_id"]
        assert second.json()["run"]["input"]["conversation_prompts"] == [
            "记住项目：北斗",
            "它叫什么？",
        ]
        assert second.json()["run"]["input"]["prompt"] == "它叫什么？"
        replay = await client.post(url, headers=headers, json=second_body)
        assert replay.json()["run"]["run_id"] == second.json()["run"]["run_id"]
        fresh = await client.post(
            url,
            headers=headers,
            json={"expectedRevision": 1, "prompt": "重新测试", "idempotencyKey": "fresh"},
        )
        assert fresh.json()["run"]["session_id"] != first.json()["run"]["session_id"]
        view = await client.get(
            f"{url}/{second.json()['run']['run_id']}?draftRevision=1", headers=headers
        )
        assert view.json()["activity"]["run_id"] == second.json()["run"]["run_id"]
        assert view.json()["loop"][3]["status"] == "skipped"
        changed = await client.put(
            f"/v1/studio/drafts/{draft['draftId']}",
            headers=headers,
            json={"expectedRevision": 1, "spec": draft["spec"]},
        )
        assert changed.status_code == 200, changed.text
        stale = await client.post(
            url,
            headers=headers,
            json={**second_body, "expectedRevision": 2, "idempotencyKey": "stale"},
        )
        assert stale.status_code == 404, stale.text


@pytest.mark.asyncio
async def test_preview_cannot_continue_running_or_foreign_draft_session() -> None:
    application, _ = app_and_container(auto_execute=False)
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "preview-isolation",
        "X-User-ID": "builder-a",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        first_draft = (
            await client.post(
                "/v1/studio/drafts", headers=headers, json=draft_request("first-preview")
            )
        ).json()
        second_draft = (
            await client.post(
                "/v1/studio/drafts", headers=headers, json=draft_request("second-preview")
            )
        ).json()
        url = f"/v1/studio/drafts/{first_draft['draftId']}/try-runs"
        first = await client.post(
            url,
            headers=headers,
            json={"expectedRevision": 1, "prompt": "hello", "idempotencyKey": "first"},
        )
        body = {
            "expectedRevision": 1,
            "prompt": "next",
            "idempotencyKey": "next",
            "continueFromRunId": first.json()["run"]["run_id"],
        }
        active = await client.post(url, headers=headers, json=body)
        assert active.status_code == 409, active.text
        other_draft = await client.post(
            f"/v1/studio/drafts/{second_draft['draftId']}/try-runs", headers=headers, json=body
        )
        assert other_draft.status_code == 404, other_draft.text
        foreign = await client.post(url, headers={**headers, "X-User-ID": "builder-b"}, json=body)
        assert foreign.status_code in {403, 404}, foreign.text
        missing_file = await client.post(
            url,
            headers=headers,
            json={
                "expectedRevision": 1,
                "prompt": "read",
                "idempotencyKey": "file",
                "inputArtifactIds": ["input_artifact_missing"],
            },
        )
        assert missing_file.status_code == 404, missing_file.text


@pytest.mark.asyncio
async def test_preview_accepts_owned_attachments_and_rejects_other_users_files() -> None:
    application, _ = app_and_container(auto_execute=False)
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "preview-files",
        "X-User-ID": "builder-a",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        draft = (
            await client.post(
                "/v1/studio/drafts", headers=headers, json=draft_request("files-preview")
            )
        ).json()
        upload = await client.post(
            "/v1/input-artifacts",
            headers=headers,
            files={"file": ("notes.txt", b"original material", "text/plain")},
        )
        assert upload.status_code == 201, upload.text
        artifact_id = upload.json()["input_artifact_id"]
        url = f"/v1/studio/drafts/{draft['draftId']}/try-runs"
        body = {
            "expectedRevision": 1,
            "prompt": "Read attached material",
            "idempotencyKey": "own-file",
            "inputArtifactIds": [artifact_id],
        }
        accepted = await client.post(url, headers=headers, json=body)
        assert accepted.status_code == 202, accepted.text
        assert accepted.json()["run"]["input"]["input_artifact_ids"] == [artifact_id]
        foreign_upload = await client.post(
            "/v1/input-artifacts",
            headers={**headers, "X-User-ID": "builder-b"},
            files={"file": ("private.txt", b"private", "text/plain")},
        )
        rejected = await client.post(
            url,
            headers=headers,
            json={
                **body,
                "idempotencyKey": "foreign-file",
                "inputArtifactIds": [foreign_upload.json()["input_artifact_id"]],
            },
        )
        assert rejected.status_code == 404, rejected.text


@pytest.mark.asyncio
async def test_preview_images_route_to_vision_and_followups_keep_that_route() -> None:
    from unittest.mock import AsyncMock

    from harness.studio.api import get_model_configuration_service

    application, container = app_and_container(auto_execute=False)
    models = AsyncMock()
    models.resolve_runtime.return_value = object()
    application.dependency_overrides[get_model_configuration_service] = lambda: models
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "preview-image",
        "X-User-ID": "builder-a",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        draft = (
            await client.post(
                "/v1/studio/drafts", headers=headers, json=draft_request("image-preview")
            )
        ).json()
        upload = await client.post(
            "/v1/input-artifacts",
            headers=headers,
            files={"file": ("red.png", image_bytes(), "image/png")},
        )
        url = f"/v1/studio/drafts/{draft['draftId']}/try-runs"
        response = await client.post(
            url,
            headers=headers,
            json={
                "expectedRevision": 1,
                "prompt": "describe",
                "idempotencyKey": "image",
                "inputArtifactIds": [upload.json()["input_artifact_id"]],
            },
        )
        assert response.status_code == 202, response.text
        started = await container.runs.get("preview-image", response.json()["run"]["run_id"])
        assert started.input["required_model_capabilities"] == ["vision"]
        assert started.input["model_route_override"]
        models.resolve_runtime.assert_awaited()
        assert models.resolve_runtime.call_args.kwargs["apply_agent_binding"] is False
        await container.worker.execute("preview-image", started.run_id)
        followup = await client.post(
            url,
            headers=headers,
            json={
                "expectedRevision": 1,
                "prompt": "继续解释",
                "idempotencyKey": "followup-image",
                "continueFromRunId": started.run_id,
            },
        )
        assert followup.status_code == 202, followup.text
        assert (
            followup.json()["run"]["input"]["model_route_override"]
            == started.input["model_route_override"]
        )
        models.resolve_runtime.return_value = None
        rejected = await client.post(
            url,
            headers=headers,
            json={
                "expectedRevision": 1,
                "prompt": "describe",
                "idempotencyKey": "no-vision-credential",
                "inputArtifactIds": [upload.json()["input_artifact_id"]],
            },
        )
        assert rejected.status_code == 409, rejected.text
        assert "凭据" in rejected.text


@pytest.mark.asyncio
async def test_builder_materials_reads_owned_text_and_rejects_other_owner() -> None:
    application, _ = app_and_container(auto_execute=False)
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "builder-materials",
        "X-User-ID": "builder-a",
    }
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        upload = await client.post(
            "/v1/input-artifacts",
            headers=headers,
            files={"file": ("reference.txt", b"Use a three-column table", "text/plain")},
        )
        body = {"inputArtifactIds": [upload.json()["input_artifact_id"]]}
        result = await client.post("/v1/studio/builder-materials", headers=headers, json=body)
        assert result.status_code == 200, result.text
        assert "three-column table" in result.json()["context"]
        foreign = await client.post(
            "/v1/studio/builder-materials", headers={**headers, "X-User-ID": "builder-b"}, json=body
        )
        assert foreign.status_code == 404, foreign.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,media_type", [("zip", "application/zip"), ("rar", "application/vnd.rar")]
)
async def test_nexau_archive_import_opens_an_editable_draft(kind: str, media_type: str) -> None:
    from tests.unit.studio.test_nexau_import import archive_bytes

    application, _ = app_and_container(auto_execute=False)
    headers = {
        "Authorization": f"Bearer {SERVICE_TOKEN}",
        "X-Tenant-ID": "archive-import",
        "X-User-ID": "builder-a",
    }
    content = archive_bytes(
        {
            "bundle/code_agent.yaml": (
                b"name: archive-demo\nsystem_prompt_type: string\nsystem_prompt: Answer briefly.\n"
            )
        },
        kind,
    )
    async with AsyncClient(
        transport=ASGITransport(app=application), base_url="http://test"
    ) as client:
        imported = await client.post(
            "/v1/studio/drafts/import",
            content=content,
            headers={**headers, "Content-Type": media_type},
        )
        assert imported.status_code == 201, imported.text
        draft = imported.json()["draft"]
        assert draft["spec"]["name"] == "archive-demo"
        assert draft["spec"]["skills"] == []
        saved = await client.get(f"/v1/studio/drafts/{draft['draftId']}", headers=headers)
        assert saved.status_code == 200
        assert "Answer briefly." in saved.json()["spec"]["systemPrompt"]
        invalid = await client.post(
            "/v1/studio/drafts/import",
            content=b"Rar!\x1a\x07\x00broken",
            headers={**headers, "Content-Type": "application/vnd.rar"},
        )
        assert invalid.status_code == 422, invalid.text
        assert "RAR" in invalid.text
