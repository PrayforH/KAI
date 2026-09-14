import io
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from zipfile import ZipFile

import pytest

from harness.api.dependencies import build_memory_container
from harness.core.errors import ConflictError
from harness.core.models import RunStatus
from harness.studio.models import CreateAgentDraftRequest
from harness.studio.skill_builder import SkillConversationContext, SkillConversationRequest
from harness.studio.worker_skill_creator import WorkerSkillCreator


def archive() -> bytes:
    result = io.BytesIO()
    with ZipFile(result, "w") as zipped:
        zipped.writestr(
            "sample-skill/SKILL.md",
            "---\nname: sample-skill\ndescription: 核验来源时使用\n---\n核验资料并引用。",
        )
        zipped.writestr("sample-skill/assets/template.md", "来源 | 结论")
    return result.getvalue()


@pytest.mark.asyncio
@pytest.mark.parametrize("has_trace", [True, False])
async def test_creator_compiles_complete_package_and_requires_real_trace(has_trace: bool) -> None:
    container = build_memory_container()
    draft = await container.studio.create(
        tenant_id="tenant-a",
        user_id="user-a",
        request=CreateAgentDraftRequest(
            name="test-agent", domain="general", displayName="测试", description="测试技能创建"
        ),
    )
    runs, artifacts, events, agents, sessions = (AsyncMock() for _ in range(5))
    runs.create_with_result.return_value = SimpleNamespace(
        created=True, run=SimpleNamespace(run_id="creator-run")
    )
    runs.get.return_value = SimpleNamespace(status=RunStatus.SUCCEEDED)
    sessions.create.return_value = SimpleNamespace(session_id="creator-session")
    events.list_after.return_value = (
        [
            SimpleNamespace(type="tool.request", payload=payload)
            for payload in (
                {"tool_call_id": "t1", "name": "Skill", "arguments": {"skill": "skill-creator"}},
                {
                    "tool_call_id": "t2",
                    "name": "Bash",
                    "arguments": {
                        "command": ("python -m scripts.package_skill "
                                    "/workspace/authored/sample-skill /workspace/out")
                    },
                },
            )
        ]
        if has_trace
        else []
    )
    if has_trace:
        events.list_after.return_value += [
            SimpleNamespace(
                type="tool.result", payload={"tool_call_id": call_id, "is_error": False}
            )
            for call_id in ("t1", "t2")
        ]
    artifacts.list_for_run.return_value = [
        SimpleNamespace(name="sample-skill.skill", artifact_id="a1"),
        SimpleNamespace(name="evals.json", artifact_id="a2"),
    ]
    artifacts.download.side_effect = [
        (None, archive()),
        (None, b'{"evals":[{"prompt":"check source"},{"prompt":"check missing source"}]}'),
    ]
    container = replace(
        container,
        runs=runs,
        artifacts=artifacts,
        observed_events=events,
        agents=agents,
        sessions=sessions,
        auto_execute=False,
    )
    authorize = Mock()
    creator = WorkerSkillCreator(container, draft, "user-a", authorize)
    request = SkillConversationRequest(
        modelRoute=draft.spec.model.route_id,
        context=SkillConversationContext(
            agentName="test-agent",
            displayName="测试",
            domain="general",
            description="创建资料核验技能",
        ),
        messages=({"role": "user", "content": "生成一个资料核验技能"},),
    )
    if not has_trace:
        with pytest.raises(ConflictError, match="未验证"):
            await creator.respond("tenant-a", request, name="sample-skill")
        artifacts.download.assert_not_awaited()
    else:
        result = await creator.respond("tenant-a", request, name="sample-skill")
        assert result.skill and result.skill.name == "sample-skill"
        assert result.creator_run_id == "creator-run"
        assert result.creator_source_revision == "41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f"
    authorize.assert_called_once()
    snapshot = agents.register_preview_snapshot.call_args.args[2]
    text = str(snapshot.model_dump())
    assert "scripts/package_skill.py" in text and "eval-viewer/generate_review.py" in text
    assert (await container.studio.get("tenant-a", "user-a", draft.draft_id)).revision == 1
