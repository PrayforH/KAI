from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from harness.agui.routes import _visible_thread_runs
from harness.assistant.conversation import PERSONAL_ASSISTANT_THREAD, PersonalConversation
from harness.core.errors import ConflictError, NotFoundError
from harness.feishu.service import is_command


def setup():
    agui, runs, agents, events = [AsyncMock() for _ in range(4)]
    service = PersonalConversation(agui=agui, runs=runs, agents=agents, events=events)
    agents.ensure_user_default.return_value = SimpleNamespace(name="assistant", version="1")
    agui.get_thread_record.side_effect = NotFoundError("new")
    agui.create_run_with_result.return_value = SimpleNamespace(
        run=SimpleNamespace(run_id="created")
    )
    runs.list_for_sessions.return_value = []
    return service


@pytest.mark.asyncio
async def test_existing_web_conversation_preserves_selected_agent_and_owner_scope():
    s = setup()
    s.agui.get_thread_record.side_effect = None
    s.agui.get_thread_record.return_value = SimpleNamespace(session_ids=("old", "current"))
    s.runs.find_by_idempotency_key.return_value = None
    s.agui.get_binding.return_value = SimpleNamespace(
        agent_name="chosen", agent_version="2", agent_owner_user_id="user-a", space_id=None
    )
    await s.submit("tenant-a", "user-a", "message-key", "任何话题都可以吗？")
    args = s.agui.create_run_with_result.call_args.kwargs
    assert args["agent_name"] == "chosen" and args["agent_version"] == "2"
    assert args["tenant_id"] == "tenant-a" and args["user_id"] == "user-a"
    assert args["request"].thread_id == PERSONAL_ASSISTANT_THREAD
    assert args["request"].run_id == "message-key"
    assert args["request"].messages[0].content == "任何话题都可以吗？"
    s.agents.ensure_user_default.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_finds_key_in_retained_session_without_submitting_again():
    s = setup()
    s.agui.get_thread_record.side_effect = None
    s.agui.get_thread_record.return_value = SimpleNamespace(session_ids=("old", "current"))
    recovered = SimpleNamespace(run_id="existing")
    s.runs.find_by_idempotency_key.side_effect = [None, recovered]
    assert await s.submit("tenant", "user", "same-key", "你好") is recovered
    assert [c.args for c in s.runs.find_by_idempotency_key.call_args_list] == [
        ("tenant", "current", "same-key"),
        ("tenant", "old", "same-key"),
    ]
    s.agui.create_run_with_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_external_append_preserves_the_current_web_branch_and_all_previous_turns():
    s = setup()
    s.agui.get_thread_record.side_effect = None
    s.agui.get_thread_record.return_value = SimpleNamespace(session_ids=("current",))
    s.runs.find_by_idempotency_key.return_value = None
    s.agui.get_binding.return_value = SimpleNamespace(
        agent_name="chosen", agent_version="2", agent_owner_user_id="user", space_id=None
    )
    now = datetime.now(UTC)
    s.runs.list_for_sessions.return_value = [
        SimpleNamespace(
            run_id="first",
            created_at=now - timedelta(seconds=1),
            input={"prompt": "记住代号 ABC"},
        ),
        SimpleNamespace(run_id="edited", created_at=now, input={"prompt": "废弃的分支"}),
        SimpleNamespace(
            run_id="latest",
            created_at=now + timedelta(seconds=1),
            input={
                "prompt": "刷新后追问",
                "conversation_prompts": ["记住代号 ABC", "刷新后追问"],
            },
        ),
    ]
    await s.submit("tenant", "user", "external", "从飞书继续聊")
    request = s.agui.create_run_with_result.call_args.kwargs["request"]
    assert [message.content for message in request.messages] == [
        "记住代号 ABC",
        "刷新后追问",
        "从飞书继续聊",
    ]
    assert request.messages[-1].id == "external"
    appended = SimpleNamespace(
        run_id="external",
        created_at=now + timedelta(seconds=2),
        input={
            "prompt": "从飞书继续聊",
            "conversation_prompts": [message.content for message in request.messages],
        },
    )
    assert [
        run.run_id
        for run in _visible_thread_runs(
            [
                *s.runs.list_for_sessions.return_value,
                appended,
            ]
        )
    ] == ["first", "latest", "external"]


@pytest.mark.asyncio
async def test_missing_default_never_creates_an_unscoped_conversation():
    s = setup()
    s.agents.ensure_user_default.return_value = None
    with pytest.raises(ConflictError):
        await s.submit("tenant", "user", "key", "你好")
    s.agui.create_run_with_result.assert_not_awaited()


@pytest.mark.parametrize(
    "content", ["你好", "状态怎么样", "新建一个网站", "帮我写一段话", "任何话题", "1+1等于多少"]
)
def test_ordinary_text_is_not_limited_to_command_vocabulary(content):
    assert not is_command(content)


@pytest.mark.parametrize(
    "content", ["事项", "新建 标题", "执行 标题", "帮助", "补充 标题\n具体要求"]
)
def test_explicit_existing_commands_remain_available(content):
    assert is_command(content)
