"""Project resources travel with every Run created under the project."""

from __future__ import annotations

from pathlib import Path

import pytest
from ag_ui.core import RunAgentInput, UserMessage

from harness.agui.service import _merge_knowledge_references
from harness.api.dependencies import build_memory_container
from harness.core.errors import NotFoundError
from harness.projects.models import ProjectRunContext


def test_project_knowledge_is_added_to_a_task_selection() -> None:
    context = ProjectRunContext(
        projectId="project-1",
        knowledgeReferences=("kb-project", "kb-shared"),
    )
    assert _merge_knowledge_references(
        ["kb-shared", "kb-task"], context
    ) == ["kb-shared", "kb-task", "kb-project"]
    assert _merge_knowledge_references(None, context) == [
        "kb-project",
        "kb-shared",
    ]
    assert _merge_knowledge_references(["kb-task"], None) == ["kb-task"]


async def _publish_agent(container, tenant_id: str, user_id: str, root: Path) -> None:
    agent_dir = root / "echo-agent"
    (agent_dir / "prompts").mkdir(parents=True)
    (agent_dir / "prompts" / "system.md").write_text("You are a test agent.", encoding="utf-8")
    (agent_dir / "agent.yaml").write_text(
        "\n".join(
            [
                "apiVersion: harness/v1alpha1",
                "kind: Agent",
                "metadata:",
                "  name: echo-agent",
                "  version: 0.1.0",
                "spec:",
                "  runtime: claude-agent-sdk",
                "  model:",
                "    route: new-api-default",
                "    model: claude-sonnet-4-6",
                "  prompt:",
                "    system: prompts/system.md",
                "  permissions:",
                "    policy: local-standard",
                "",
            ]
        ),
        encoding="utf-8",
    )
    await container.agents.publish(tenant_id, user_id, agent_dir / "agent.yaml")


def _request(thread_id: str) -> RunAgentInput:
    return RunAgentInput(
        thread_id=thread_id,
        run_id="client-run-1",
        messages=[UserMessage(id="msg-1", content="汇总项目进展")],
        tools=[],
        state=[],
        context=[],
        forwarded_props={},
    )


@pytest.mark.asyncio
async def test_run_captures_instructions_memories_and_documents(tmp_path: Path) -> None:
    container = build_memory_container()
    tenant_id, user_id = "tenant-a", "user-1"
    await _publish_agent(container, tenant_id, user_id, tmp_path)
    project = await container.projects.create(
        tenant_id=tenant_id,
        user_id=user_id,
        name="金融办",
        instructions="先给证据，再给结论",
        skill_references=("pptx-generator@1",),
    )
    await container.projects.add_memory(
        tenant_id=tenant_id,
        user_id=user_id,
        project_id=project.project_id,
        content="口径以 2026 版为准",
    )
    artifact = await container.input_artifacts.upload(
        tenant_id=tenant_id,
        user_id=user_id,
        name="brief.md",
        media_type="text/markdown",
        content=b"# briefing",
    )
    await container.projects.attach_document(
        tenant_id=tenant_id,
        user_id=user_id,
        project_id=project.project_id,
        input_artifact_id=artifact.input_artifact_id,
    )
    await container.agui.create_project_thread(
        tenant_id=tenant_id,
        user_id=user_id,
        thread_id="thread-project",
        agent_name="echo-agent",
        agent_version="0.1.0",
        agent_owner_user_id=user_id,
        space_id=None,
        connection_mode="caller_owned",
        project_id=project.project_id,
    )

    creation = await container.agui.create_run_with_result(
        tenant_id=tenant_id,
        user_id=user_id,
        agent_name="echo-agent",
        agent_version="0.1.0",
        request=_request("thread-project"),
    )

    run_input = creation.run.input
    assert "先给证据，再给结论" in str(run_input["project_context"])
    assert "口径以 2026 版为准" in str(run_input["project_context"])
    assert "## 项目技能" in str(run_input["project_context"])
    assert run_input["project_skill_references"] == ["pptx-generator@1"]
    assert run_input["project_context_meta"] == {
        "projectId": project.project_id,
        "memories": 1,
        "documents": 1,
        "skills": 1,
        "knowledge": 0,
        "links": 0,
    }
    assert run_input["input_artifact_ids"] == []
    assert run_input["project_document_ids"] == [artifact.input_artifact_id]


@pytest.mark.asyncio
async def test_task_outliving_its_project_runs_without_project_context(
    tmp_path: Path,
) -> None:
    container = build_memory_container()
    tenant_id, user_id = "tenant-a", "user-1"
    await _publish_agent(container, tenant_id, user_id, tmp_path)
    project = await container.projects.create(
        tenant_id=tenant_id,
        user_id=user_id,
        name="临时项目",
        instructions="只在项目存在时生效",
    )
    await container.agui.create_project_thread(
        tenant_id=tenant_id,
        user_id=user_id,
        thread_id="thread-detached",
        agent_name="echo-agent",
        agent_version="0.1.0",
        agent_owner_user_id=user_id,
        space_id=None,
        connection_mode="caller_owned",
        project_id=project.project_id,
    )
    await container.projects.delete(
        tenant_id=tenant_id, user_id=user_id, project_id=project.project_id
    )

    creation = await container.agui.create_run_with_result(
        tenant_id=tenant_id,
        user_id=user_id,
        agent_name="echo-agent",
        agent_version="0.1.0",
        request=_request("thread-detached"),
    )

    assert "project_context" not in creation.run.input
    assert creation.run.input["input_artifact_ids"] == []


@pytest.mark.asyncio
async def test_first_run_binds_the_project_without_an_empty_task(
    tmp_path: Path,
) -> None:
    container = build_memory_container()
    tenant_id, user_id = "tenant-a", "user-1"
    await _publish_agent(container, tenant_id, user_id, tmp_path)
    project = await container.projects.create(
        tenant_id=tenant_id,
        user_id=user_id,
        name="金融办",
        instructions="项目级指令",
    )

    creation = await container.agui.create_run_with_result(
        tenant_id=tenant_id,
        user_id=user_id,
        agent_name="echo-agent",
        agent_version="0.1.0",
        request=_request("thread-first-run"),
        project_id=project.project_id,
    )

    stored = await container.agui.get_thread_record(
        tenant_id=tenant_id, user_id=user_id, thread_id="thread-first-run"
    )
    assert stored.project_id == project.project_id
    assert "项目级指令" in str(creation.run.input["project_context"])


@pytest.mark.asyncio
async def test_run_rejects_a_project_the_user_cannot_see(tmp_path: Path) -> None:
    container = build_memory_container()
    tenant_id, user_id = "tenant-a", "user-1"
    await _publish_agent(container, tenant_id, user_id, tmp_path)

    with pytest.raises(NotFoundError):
        await container.agui.create_run_with_result(
            tenant_id=tenant_id,
            user_id=user_id,
            agent_name="echo-agent",
            agent_version="0.1.0",
            request=_request("thread-invalid-project"),
            project_id="project_missing",
        )
