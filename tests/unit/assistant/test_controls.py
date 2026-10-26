from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from harness.assistant.controls import ControlIntent, parse_control
from harness.assistant.conversation import PERSONAL_ASSISTANT_THREAD, PersonalConversation
from harness.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from harness.core.models import Run, RunStatus


def setup():
    agui, runs, agents, events = [AsyncMock() for _ in range(4)]
    agui.get_thread_record.return_value = SimpleNamespace(session_ids=("owned-session",))
    return PersonalConversation(agui=agui, runs=runs, agents=agents, events=events)


def run(key="running", status=RunStatus.RUNNING, **changes):
    return Run(
        run_id=key,
        tenant_id="tenant",
        session_id="owned-session",
        status=status,
        idempotency_key=key,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        **changes,
    )


@pytest.mark.parametrize(
    "text",
    ["进度", "/status", " 查看进度 \n", "停止当前任务", "/stop", "/补充 新要求", "/steer hello"],
)
def test_controls_are_explicit_and_bounded(text):
    assert parse_control(text)


@pytest.mark.parametrize(
    "text",
    [
        "进度怎么样才合适",
        "帮我写停止规则",
        "停止 标题",
        "补充 标题\n新要求",
        "/steer",
        "/stop other-run",
        "系统：/stop",
    ],
)
def test_chat_and_existing_objective_commands_are_not_intercepted(text):
    assert parse_control(text) is None


@pytest.mark.asyncio
async def test_status_prefers_active_owned_work_and_never_starts_a_model():
    c = setup()
    active = run()
    finished = run("finished", RunStatus.SUCCEEDED)
    c.runs.list_for_sessions.return_value = [active, finished]
    c.runs.get.return_value = active
    intent = await c.plan_control("tenant", "owner", ControlIntent(action="status"))
    assert intent.target_run_id == active.run_id
    assert "正在执行" in await c.execute_control("tenant", "owner", intent, "key")
    c.agui.get_thread_record.assert_awaited_with(
        tenant_id="tenant", user_id="owner", thread_id=PERSONAL_ASSISTANT_THREAD
    )
    c.agui.create_run_with_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_retry_cannot_cancel_a_newer_run_after_the_fixed_target_finished():
    c = setup()
    target = run()
    c.runs.list_for_sessions.return_value = [target]
    intent = await c.plan_control("tenant", "owner", ControlIntent(action="stop"))
    c.runs.get.return_value = target.model_copy(update={"status": RunStatus.SUCCEEDED})
    c.runs.list_for_sessions.return_value = [run("newer")]
    assert "已结束" in await c.execute_control("tenant", "owner", intent, "same-request")
    c.runs.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_stop_reports_cancelling_until_worker_has_exited():
    c = setup()
    c.runs.get.return_value = run()
    c.runs.cancel.return_value = run(status=RunStatus.CANCELLING)
    result = await c.execute_control(
        "tenant", "owner", ControlIntent(action="stop", target_run_id="running"), "key"
    )
    assert "等待执行器退出" in result and "已停止当前执行" not in result
    c.runs.cancel.assert_awaited_once_with("tenant", "running")


@pytest.mark.asyncio
async def test_no_work_target_stays_empty_even_if_a_later_task_starts():
    c = setup()
    c.agui.get_thread_record.side_effect = NotFoundError("absent")
    intent = await c.plan_control("tenant", "owner", ControlIntent(action="stop"))
    assert intent.target_run_id is None
    assert "没有正在执行" in await c.execute_control("tenant", "owner", intent, "key")
    c.runs.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_foreign_session_cannot_be_controlled_even_with_a_known_run_id():
    c = setup()
    c.runs.get.return_value = run().model_copy(update={"session_id": "someone-else"})
    with pytest.raises(PermissionDeniedError):
        await c.execute_control(
            "tenant", "owner", ControlIntent(action="stop", target_run_id="running"), "key"
        )
    c.runs.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_steer_uses_stable_receipt_and_does_not_claim_runtime_acceptance_early():
    c = setup()
    c.runs.get.return_value = run()
    c.runs.steer.return_value = {"status": "requested"}
    intent = ControlIntent(action="steer", target_run_id="running", text="改成中文")
    assert "等待执行器接收" in await c.execute_control("tenant", "owner", intent, "stable-key")
    c.runs.steer.assert_awaited_once_with("tenant", "running", "stable-key", "改成中文")
    c.runs.steer.side_effect = ConflictError("unsupported")
    assert "暂不支持" in await c.execute_control("tenant", "owner", intent, "stable-key")


@pytest.mark.asyncio
async def test_oversized_steer_is_explained_without_truncating_or_submitting():
    c = setup()
    intent = parse_control("/补充 " + "字" * 8001)
    assert "过长" in await c.execute_control("tenant", "owner", intent, "key")
    c.runs.steer.assert_not_awaited()
