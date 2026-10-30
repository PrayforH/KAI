"""渠道引用 → 内部消息映射的契约（不猜、不越权、找不到就空）。"""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from harness.assistant.constants import PERSONAL_ASSISTANT_THREAD
from harness.assistant.conversation import PersonalConversation
from harness.core.errors import NotFoundError
from harness.core.models import Run, RunStatus

NOW = datetime(2026, 10, 30, 12, tzinfo=UTC)


def _run(run_id: str, prompt: str) -> Run:
    return Run(
        run_id=run_id,
        session_id="session-1",
        tenant_id="t",
        status=RunStatus.SUCCEEDED,
        idempotency_key=f"feishu-chat-{run_id}",
        created_at=NOW,
        updated_at=NOW,
        input={"prompt": prompt},
    )


class _Agui:
    def __init__(self, sessions: tuple[str, ...] = ("session-1",)) -> None:
        self._sessions = sessions

    async def get_thread_record(self, **_kwargs: Any) -> Any:
        return SimpleNamespace(session_ids=self._sessions)


class _Runs:
    def __init__(self, runs: dict[tuple[str, str], Run]) -> None:
        self._runs = runs
        self.asked: list[tuple[str, str, str]] = []

    async def find_by_idempotency_key(self, tenant_id: str, session_id: str, key: str):
        self.asked.append((tenant_id, session_id, key))
        return self._runs.get((session_id, key))


def _conversation(runs: _Runs, agui: _Agui | None = None) -> PersonalConversation:
    return PersonalConversation(
        agui=agui or _Agui(),  # type: ignore[arg-type]
        runs=runs,  # type: ignore[arg-type]
        agents=SimpleNamespace(),  # type: ignore[arg-type]
        events=SimpleNamespace(),  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_platform_message_maps_to_the_internal_message_id() -> None:
    runs = _Runs({("session-1", "feishu-chat-om_original"): _run("run-1", "把报价单转成 PDF")})
    conversation = _conversation(runs)
    reference = await conversation.reply_reference(
        "t", "u", platform_message_id="om_original", prefix="feishu-chat-"
    )
    assert reference == {"messageId": "user-run-1", "excerpt": "把报价单转成 PDF"}


@pytest.mark.asyncio
async def test_unknown_platform_message_yields_no_reference_instead_of_a_guess() -> None:
    runs = _Runs({})
    conversation = _conversation(runs)
    assert (
        await conversation.reply_reference(
            "t", "u", platform_message_id="om_unknown", prefix="feishu-chat-"
        )
        is None
    )
    # 只在该用户自己的会话里查，不会去猜别的线程
    assert all(item[0] == "t" for item in runs.asked)


@pytest.mark.asyncio
async def test_missing_thread_yields_no_reference() -> None:
    class Missing:
        async def get_thread_record(self, **_kwargs: Any) -> Any:
            raise NotFoundError("no personal thread yet")

    conversation = _conversation(_Runs({}), Missing())  # type: ignore[arg-type]
    assert (
        await conversation.reply_reference(
            "t", "u", platform_message_id="om_x", prefix="feishu-chat-"
        )
        is None
    )


@pytest.mark.asyncio
async def test_weixin_key_reference_uses_the_precomputed_key() -> None:
    runs = _Runs({("session-1", "weixin-chat-abc"): _run("run-9", "继续那个报表")})
    conversation = _conversation(runs)
    reference = await conversation.reply_reference_by_key(
        "t", "u", key="weixin-chat-abc"
    )
    assert reference == {"messageId": "user-run-9", "excerpt": "继续那个报表"}
    assert PERSONAL_ASSISTANT_THREAD  # 线程常量即个人助理主会话
