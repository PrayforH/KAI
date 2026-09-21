import io
from collections.abc import Sequence
from dataclasses import dataclass, replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock
from zipfile import ZipFile

import pytest

from harness.api.dependencies import build_memory_container
from harness.core.errors import ConflictError
from harness.core.models import RunStatus
from harness.studio.models import CreateAgentDraftRequest
from harness.studio.skill_builder import SkillConversationContext, SkillConversationRequest
from harness.studio.worker_skill_creator import WorkerSkillCreator

USER_MESSAGE = "生成一个资料核验技能"
USER_DESCRIPTION = "创建资料核验技能"


def archive() -> bytes:
    result = io.BytesIO()
    with ZipFile(result, "w") as zipped:
        zipped.writestr(
            "sample-skill/SKILL.md",
            "---\nname: sample-skill\ndescription: 核验来源时使用\n---\n核验资料并引用。",
        )
        zipped.writestr("sample-skill/assets/template.md", "来源 | 结论")
    return result.getvalue()


def creator_call(name: str, arguments: dict[str, Any], call_id: str) -> dict[str, Any]:
    return {"tool_call_id": call_id, "name": name, "arguments": arguments}


SKILL_LOADED = creator_call("Skill", {"skill": "skill-creator"}, "t1")
PACKAGED = creator_call(
    "Bash",
    {"command": "python -m scripts.package_skill /workspace/authored/sample-skill /workspace/out"},
    "t2",
)


@dataclass
class Creator:
    """One arranged authoring run, with the container's collaborators recorded."""

    container: Any
    draft: Any
    creator: WorkerSkillCreator
    request: SkillConversationRequest
    runs: AsyncMock
    artifacts: AsyncMock
    agents: AsyncMock
    sessions: AsyncMock

    def snapshot(self) -> Any:
        return self.agents.register_preview_snapshot.call_args.args[2]

    def task_prompt(self) -> str:
        return str(self.runs.create_with_result.call_args.kwargs["input"]["prompt"])


async def arrange_creator(*, calls: Sequence[dict[str, Any]]) -> Creator:
    """Arrange one authoring run whose tool calls all succeeded."""

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
    events.list_after.return_value = [
        SimpleNamespace(type="tool.request", payload=payload) for payload in calls
    ] + [
        SimpleNamespace(
            type="tool.result",
            payload={"tool_call_id": call["tool_call_id"], "is_error": False},
        )
        for call in calls
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
    request = SkillConversationRequest(
        modelRoute=draft.spec.model.route_id,
        context=SkillConversationContext(
            agentName="test-agent",
            displayName="测试",
            domain="general",
            description=USER_DESCRIPTION,
        ),
        messages=({"role": "user", "content": USER_MESSAGE},),
    )
    return Creator(
        container=container,
        draft=draft,
        creator=WorkerSkillCreator(container, draft, "user-a", Mock()),
        request=request,
        runs=runs,
        artifacts=artifacts,
        agents=agents,
        sessions=sessions,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("has_trace", [True, False])
async def test_creator_compiles_complete_package_and_requires_real_trace(has_trace: bool) -> None:
    arranged = await arrange_creator(calls=(SKILL_LOADED, PACKAGED) if has_trace else ())
    container, draft, creator, request = (
        arranged.container,
        arranged.draft,
        arranged.creator,
        arranged.request,
    )
    if not has_trace:
        with pytest.raises(ConflictError, match="未验证"):
            await creator.respond("tenant-a", request, name="sample-skill")
        arranged.artifacts.download.assert_not_awaited()
    else:
        result = await creator.respond("tenant-a", request, name="sample-skill")
        assert result.skill and result.skill.name == "sample-skill"
        assert result.creator_run_id == "creator-run"
        assert result.creator_source_revision == "41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f"
    creator.authorize.assert_called_once()
    assert arranged.agents.register_preview_snapshot.call_args.kwargs["agent_id"] == draft.agent_id
    assert arranged.sessions.create.call_args.args[2] == draft.spec.name
    text = str(arranged.snapshot().model_dump())
    assert "scripts/package_skill.py" in text and "eval-viewer/generate_review.py" in text
    assert (await container.studio.get("tenant-a", "user-a", draft.draft_id)).revision == 1


@pytest.mark.asyncio
async def test_creator_keeps_user_words_out_of_the_system_prompt() -> None:
    """The instructions are a code-owned contract; the request travels as data.

    The authoring run carries Write/Bash, so anything the conversation said belongs
    in the task message — labelled untrusted — and never in the system prompt.
    """

    arranged = await arrange_creator(calls=(SKILL_LOADED, PACKAGED))

    await arranged.creator.respond("tenant-a", arranged.request, name="sample-skill")

    system_prompt = arranged.snapshot().system_prompt
    task = arranged.task_prompt()
    assert USER_MESSAGE not in system_prompt
    assert USER_DESCRIPTION not in system_prompt
    assert "生成一个资料核验技能" in task and USER_DESCRIPTION in task
    # The artifact names the checks look for are the names the prompt asks for.
    assert "sample-skill.skill" in system_prompt
    assert "evals/evals.json" in system_prompt


@pytest.mark.asyncio
async def test_packaging_is_judged_on_the_command_not_on_any_mention() -> None:
    """Mentioning the script in an unrelated field must not count as packaging."""

    mentioned_elsewhere = creator_call(
        "Bash",
        {"command": "ls -la", "description": "then run scripts.package_skill"},
        "t2",
    )
    arranged = await arrange_creator(calls=(SKILL_LOADED, mentioned_elsewhere))

    with pytest.raises(ConflictError, match="未验证"):
        await arranged.creator.respond("tenant-a", arranged.request, name="sample-skill")
