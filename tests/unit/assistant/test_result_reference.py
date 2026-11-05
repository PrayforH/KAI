from types import SimpleNamespace

import pytest

from harness.core.errors import NotFoundError
from tests.unit.assistant.test_conversation import setup


@pytest.mark.asyncio
async def test_result_reference_resolves_original_owner_thread_and_final_message():
    service = setup()
    service.runs.get.return_value = SimpleNamespace(session_id="original-session")
    service.agui.get_thread_record_for_session.return_value = SimpleNamespace(thread_id="report")
    service.events.list_after.return_value = [
        SimpleNamespace(
            type="message.delta", payload={"text": "报告已完成", "message_id": "answer-1"}
        )
    ]
    reference = await service.result_reference("t", "u", run_id="run-1")
    assert reference == {"threadId": "report", "messageId": "answer-1", "excerpt": "报告已完成"}
    service.agui.get_thread_record_for_session.assert_awaited_once_with(
        tenant_id="t", user_id="u", session_id="original-session"
    )


@pytest.mark.asyncio
async def test_result_reference_never_exposes_other_users_session():
    service = setup()
    service.runs.get.return_value = SimpleNamespace(session_id="someone-else")
    service.agui.get_thread_record_for_session.side_effect = NotFoundError("not owned")
    assert await service.result_reference("t", "u", run_id="private") is None
    service.events.list_after.assert_not_awaited()


@pytest.mark.asyncio
async def test_quoted_submission_uses_original_project_and_preserves_idempotency():
    service = setup()
    service.agui.get_thread_record.side_effect = None
    service.agui.get_thread_record.return_value = SimpleNamespace(
        session_ids=("old",), project_id="p"
    )
    service.agui.get_binding.return_value = SimpleNamespace(
        agent_name="specialist", agent_version="2", agent_owner_user_id="u", space_id=None
    )
    service.runs.find_by_idempotency_key.return_value = None
    await service.submit(
        "t",
        "u",
        "channel-message",
        "补充预算",
        reply_to={"threadId": "report", "messageId": "answer-1"},
    )
    kwargs = service.agui.create_run_with_result.call_args.kwargs
    assert kwargs["request"].thread_id == "report"
    assert kwargs["project_id"] == "p"
    assert kwargs["agent_name"] == "specialist"
    service.agents.ensure_user_default.assert_not_awaited()
    service.runs.find_by_idempotency_key.return_value = SimpleNamespace(run_id="existing")
    assert (
        await service.submit(
            "t",
            "u",
            "channel-message",
            "补充预算",
            reply_to={"threadId": "report", "messageId": "answer-1"},
        )
    ).run_id == "existing"
    assert service.agui.create_run_with_result.await_count == 1


@pytest.mark.asyncio
async def test_deleted_quoted_task_does_not_fall_back_to_personal_thread():
    service = setup()
    with pytest.raises(NotFoundError):
        await service.submit("t", "u", "key", "继续", reply_to={"threadId": "deleted"})
    service.agui.create_run_with_result.assert_not_awaited()
    service.agents.ensure_user_default.assert_not_awaited()
