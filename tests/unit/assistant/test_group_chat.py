"""群聊 P0：花名册、@ 路由、主助理移出与隔离边界。"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from harness.assistant.models import (
    AssistantGroup,
    AssistantGroupMember,
    AssistantGroupMessage,
    AssistantGroupMessageAuthor,
    GroupMemberKind,
    Specialist,
    SpecialistStatus,
)
from harness.assistant.service import AssistantService, group_thread_id
from harness.core.errors import ConflictError, NotFoundError
from harness.core.models import RunStatus


def _specialist(specialist_id: str, name: str) -> Specialist:
    now = datetime(2026, 11, 11, tzinfo=UTC)
    return Specialist(
        specialistId=specialist_id,
        tenantId="t",
        userId="u",
        name=name,
        slug=specialist_id.replace("_", "-"),
        description="",
        agentName="agent",
        agentVersion="1.0",
        status=SpecialistStatus.READY,
        config={},
        createdAt=now,
        updatedAt=now,
    )


class FakeGroupRepository:
    def __init__(self, specialists: list[Specialist]) -> None:
        self.groups: dict[str, AssistantGroup] = {}
        self.members: dict[str, list[AssistantGroupMember]] = {}
        self.messages: dict[str, list[AssistantGroupMessage]] = {}
        self.client_ids: dict[tuple[str, str], str] = {}
        self.reactions: dict[tuple[str, str], list[str]] = {}
        self._specialists = {item.specialist_id: item for item in specialists}

    async def create_group(self, tenant_id, user_id, group, members):
        self.groups[group.group_id] = group
        self.members[group.group_id] = list(members)
        self.messages[group.group_id] = []
        return group.model_copy(update={"members": tuple(members)})

    async def list_groups(self, tenant_id, user_id):
        return [
            group.model_copy(
                update={
                    "members": tuple(
                        item for item in self.members[group.group_id]
                        if item.status == "active"
                    )
                }
            )
            for group in self.groups.values()
        ]

    async def get_group(self, tenant_id, user_id, group_id):
        if group_id not in self.groups:
            raise NotFoundError("missing")
        group = self.groups[group_id]
        return group.model_copy(
            update={
                "members": tuple(
                    item for item in self.members[group_id] if item.status == "active"
                )
            }
        )

    async def add_group_member(self, tenant_id, user_id, group_id, member):
        seats = self.members[group_id]
        for index, item in enumerate(seats):
            if item.member_id == member.member_id:
                seats[index] = member
                return
        seats.append(member)

    async def remove_group_member(self, tenant_id, user_id, group_id, member_id):
        seats = self.members[group_id]
        for index, item in enumerate(seats):
            if item.member_id == member_id:
                seats[index] = item.model_copy(update={"status": "removed"})
                break
        else:
            raise NotFoundError("missing")
        group = self.groups[group_id]
        if member_id == "primary":
            self.groups[group_id] = group.model_copy(update={"primary_present": False})
        return await self.get_group(tenant_id, user_id, group_id)

    async def set_group_reply_as(self, tenant_id, user_id, group_id, reply_as):
        group = self.groups[group_id]
        self.groups[group_id] = group.model_copy(update={"reply_as": reply_as})
        return await self.get_group(tenant_id, user_id, group_id)

    async def mark_group_read(self, tenant_id, user_id, group_id):
        group = self.groups[group_id]
        self.groups[group_id] = group.model_copy(update={"unread_count": 0})
        return await self.get_group(tenant_id, user_id, group_id)

    async def toggle_group_reaction(
        self, tenant_id, user_id, group_id, message_id, emoji
    ):
        key = (message_id, emoji)
        current = self.reactions.setdefault(key, [])
        if emoji in current:
            current.remove(emoji)
        else:
            current.append(emoji)
        return list(current)

    async def add_group_message(
        self, tenant_id, user_id, message, *, thread_id=None,
        reply_to_message_id=None, client_message_id=None,
    ):
        stored = message.model_copy(
            update={"reply_to_message_id": reply_to_message_id}
        )
        self.messages[message.group_id].append(stored)
        if client_message_id:
            self.client_ids[(message.group_id, client_message_id)] = message.message_id
        return stored

    async def list_group_messages(self, tenant_id, user_id, group_id, limit=300):
        return list(self.messages[group_id])

    async def get_group_message_by_client_id(
        self, tenant_id, user_id, group_id, client_message_id
    ):
        message_id = self.client_ids.get((group_id, client_message_id))
        return next(
            (item for item in self.messages[group_id] if item.message_id == message_id),
            None,
        )

    async def get_group_message(self, tenant_id, user_id, group_id, message_id):
        for item in self.messages[group_id]:
            if item.message_id == message_id:
                return item
        raise NotFoundError("Group message not found")

    async def group_quoted_messages(
        self, tenant_id, user_id, group_id, message_ids
    ):
        wanted = set(message_ids)
        return {
            item.message_id: item
            for item in self.messages.get(group_id, [])
            if item.message_id in wanted
        }

    async def set_group_message_run(
        self, tenant_id, user_id, group_id, message_id, *, run_id
    ):
        seats = self.messages[group_id]
        for index, item in enumerate(seats):
            if item.message_id == message_id:
                seats[index] = item.model_copy(update={"run_id": run_id})
                return
        raise NotFoundError("missing")

    async def get_specialist(self, tenant_id, user_id, specialist_id):
        try:
            return self._specialists[specialist_id]
        except KeyError as error:
            raise NotFoundError("missing") from error

    async def list_specialists(self, tenant_id, user_id, query=None):
        return list(self._specialists.values())


def _service(specialists: list[Specialist] | None = None):
    repo = FakeGroupRepository(specialists or [])
    agents, auth, projects, agui, runs, events = [AsyncMock() for _ in range(6)]
    agui.get_binding.return_value = SimpleNamespace(
        agent_name="agent", agent_version="1.0", agent_owner_user_id="u", space_id=None
    )

    async def create_run_with_result(**kwargs):
        return SimpleNamespace(run=SimpleNamespace(run_id=f"run-{kwargs['request'].run_id}"))

    agui.create_run_with_result.side_effect = create_run_with_result
    service = AssistantService(
        repo, agents=agents, auth=auth, projects=projects, agui=agui,
        runs=runs, events=events,
    )
    return service, repo, agui


@pytest.mark.asyncio
async def test_empty_group_name_is_refused_without_writing() -> None:
    service, repo, _ = _service()
    with pytest.raises(ConflictError):
        await service.create_assistant_group("t", "u", name="   ")
    assert repo.groups == {}


@pytest.mark.asyncio
async def test_group_has_creator_and_primary_by_default() -> None:
    service, _, _ = _service()
    group = await service.create_assistant_group("t", "u", name="  项目   群  ")
    assert group.name == "项目 群"
    kinds = {member.kind for member in group.members}
    assert kinds == {GroupMemberKind.CREATOR, GroupMemberKind.PRIMARY}
    assert group.primary_present is True


@pytest.mark.asyncio
async def test_plain_message_wakes_only_primary_in_group_thread() -> None:
    service, repo, agui = _service()
    group = await service.create_assistant_group("t", "u", name="项目群")
    result = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="大家好"
    )
    assert len(result.replies) == 1
    assert result.replies[0].author is AssistantGroupMessageAuthor.PRIMARY
    request = agui.create_run_with_result.await_args.kwargs["request"]
    assert request.thread_id == group_thread_id(group.group_id, "primary")
    assert request.forwarded_props["groupChat"] == group.group_id
    # 席位由服务端写入，客户端不可伪造；主助理据此获得成员管理工具。
    assert agui.create_run_with_result.await_args.kwargs["group_context"] == {
        "groupId": group.group_id,
        "memberId": "primary",
        "triggerMessageId": result.message.message_id,
        "triggerAuthor": "user",
    }
    assert all(
        item.author is not AssistantGroupMessageAuthor.SPECIALIST
        for item in repo.messages[group.group_id]
    )


@pytest.mark.asyncio
async def test_mention_wakes_only_the_named_specialist() -> None:
    specialist = _specialist("specialist_a", "资料 Bot")
    service, repo, agui = _service([specialist])
    group = await service.create_assistant_group("t", "u", name="项目群")
    await service.add_assistant_group_member(
        "t", "u", group.group_id, specialist_id=specialist.specialist_id
    )
    result = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="@资料 Bot 查一下"
    )
    assert len(result.replies) == 1
    assert result.replies[0].author is AssistantGroupMessageAuthor.SPECIALIST
    assert result.replies[0].specialist_id == "specialist_a"
    assert result.replies[0].status == "running"
    assert agui.create_run_with_result.await_count == 1
    request = agui.create_run_with_result.await_args.kwargs["request"]
    assert request.thread_id == group_thread_id(group.group_id, "specialist_a")
    assert agui.create_run_with_result.await_args.kwargs["group_context"] == {
        "groupId": group.group_id,
        "memberId": "specialist_a",
        "triggerMessageId": result.message.message_id,
        "triggerAuthor": "user",
    }
    assert "查一下" in request.messages[0].content
    assert "@资料 Bot" not in request.messages[0].content


@pytest.mark.asyncio
async def test_mentioning_a_removed_member_is_a_notice_not_a_run() -> None:
    specialist = _specialist("specialist_a", "资料 Bot")
    service, _, agui = _service([specialist])
    group = await service.create_assistant_group("t", "u", name="项目群")
    await service.add_assistant_group_member(
        "t", "u", group.group_id, specialist_id=specialist.specialist_id
    )
    await service.remove_assistant_group_member(
        "t", "u", group.group_id, member_id=specialist.specialist_id
    )
    agui.create_run_with_result.reset_mock()
    result = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="@资料 Bot 还在吗"
    )
    assert any("不在本群" in notice for notice in result.notices)
    # 只 @ 了已移出成员：提示明确，但不把消息改派给主助理。
    assert agui.create_run_with_result.await_count == 0
    assert len(result.replies) == 1
    assert result.replies[0].author is AssistantGroupMessageAuthor.SYSTEM
    assert "不在本群" in result.replies[0].text


@pytest.mark.asyncio
async def test_primary_removal_never_reroutes_plain_messages() -> None:
    service, repo, agui = _service()
    group = await service.create_assistant_group("t", "u", name="项目群")
    await service.remove_assistant_group_member("t", "u", group.group_id, member_id="primary")
    agui.create_run_with_result.reset_mock()
    result = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="还有人吗"
    )
    assert agui.create_run_with_result.await_count == 0
    assert any("不会改派" in notice for notice in result.notices)
    assert result.replies[0].author is AssistantGroupMessageAuthor.SYSTEM


@pytest.mark.asyncio
async def test_only_owned_specialists_can_join() -> None:
    service, _, _ = _service()
    group = await service.create_assistant_group("t", "u", name="项目群")
    with pytest.raises(NotFoundError):
        await service.add_assistant_group_member(
            "t", "u", group.group_id, specialist_id="specialist_other"
        )


@pytest.mark.asyncio
async def test_group_threads_are_isolated_per_group_and_member() -> None:
    left = group_thread_id("group_a", "primary")
    right = group_thread_id("group_b", "primary")
    specialist = group_thread_id("group_a", "specialist_a")
    assert len({left, right, specialist}) == 3
    assert left.startswith("assistant_group_")
    assert not left.startswith("assistant_specialist_")


@pytest.mark.asyncio
async def test_mentioning_primary_wakes_primary_in_group() -> None:
    service, _, agui = _service()
    group = await service.create_assistant_group("t", "u", name="项目群")
    result = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="@主助理 汇总一下"
    )
    assert len(result.replies) == 1
    assert result.replies[0].author is AssistantGroupMessageAuthor.PRIMARY
    request = agui.create_run_with_result.await_args.kwargs["request"]
    assert request.thread_id == group_thread_id(group.group_id, "primary")
    assert "@主助理" not in request.messages[0].content


@pytest.mark.asyncio
async def test_multi_mention_wakes_every_named_specialist() -> None:
    first = _specialist("specialist_a", "资料 Bot")
    second = _specialist("specialist_b", "数据 Bot")
    service, _, agui = _service([first, second])
    group = await service.create_assistant_group("t", "u", name="项目群")
    for item in (first, second):
        await service.add_assistant_group_member(
            "t", "u", group.group_id, specialist_id=item.specialist_id
        )
    result = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="@资料 Bot @数据 Bot 一起看看"
    )
    assert {reply.specialist_id for reply in result.replies} == {
        "specialist_a", "specialist_b"
    }
    assert agui.create_run_with_result.await_count == 2


@pytest.mark.asyncio
async def test_configured_responder_handles_plain_messages() -> None:
    specialist = _specialist("specialist_a", "资料 Bot")
    service, _, agui = _service([specialist])
    group = await service.create_assistant_group("t", "u", name="项目群")
    await service.add_assistant_group_member(
        "t", "u", group.group_id, specialist_id=specialist.specialist_id
    )
    await service.set_assistant_group_reply_as(
        "t", "u", group.group_id, reply_as=specialist.specialist_id
    )
    result = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="今天有什么进展"
    )
    assert result.replies[0].specialist_id == "specialist_a"
    assert agui.create_run_with_result.await_args.kwargs["request"].thread_id == (
        group_thread_id(group.group_id, "specialist_a")
    )


@pytest.mark.asyncio
async def test_removed_responder_falls_back_to_primary_with_group_context() -> None:
    specialist = _specialist("specialist_a", "资料 Bot")
    service, _, agui = _service([specialist])
    group = await service.create_assistant_group("t", "u", name="项目群")
    await service.add_assistant_group_member(
        "t", "u", group.group_id, specialist_id=specialist.specialist_id
    )
    await service.set_assistant_group_reply_as(
        "t", "u", group.group_id, reply_as=specialist.specialist_id
    )
    await service.remove_assistant_group_member(
        "t", "u", group.group_id, member_id=specialist.specialist_id
    )
    agui.create_run_with_result.reset_mock()
    result = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="继续"
    )
    assert agui.create_run_with_result.await_count == 1
    assert not result.notices
    assert len(result.replies) == 1
    assert result.replies[0].author is AssistantGroupMessageAuthor.PRIMARY
    call = agui.create_run_with_result.await_args.kwargs
    assert call["request"].thread_id == group_thread_id(group.group_id, "primary")
    assert call["group_context"] == {
        "groupId": group.group_id,
        "memberId": "primary",
        "triggerMessageId": result.message.message_id,
        "triggerAuthor": "user",
    }


@pytest.mark.asyncio
async def test_legacy_dangling_responder_falls_back_without_rewriting_group() -> None:
    service, repo, agui = _service()
    group = await service.create_assistant_group("t", "u", name="项目群")
    repo.groups[group.group_id] = group.model_copy(update={"reply_as": "legacy_missing"})
    result = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="改个群名，新技术预研"
    )
    assert len(result.replies) == 1
    assert result.replies[0].author is AssistantGroupMessageAuthor.PRIMARY
    assert not result.notices
    assert agui.create_run_with_result.await_count == 1
    assert repo.groups[group.group_id].reply_as == "legacy_missing"


@pytest.mark.asyncio
async def test_missing_responder_and_removed_primary_do_not_wake_another_specialist() -> None:
    specialist = _specialist("specialist_b", "资料 Bot")
    service, repo, agui = _service([specialist])
    group = await service.create_assistant_group("t", "u", name="项目群")
    await service.add_assistant_group_member(
        "t", "u", group.group_id, specialist_id=specialist.specialist_id
    )
    repo.groups[group.group_id] = group.model_copy(update={"reply_as": "legacy_missing"})
    await service.remove_assistant_group_member("t", "u", group.group_id, member_id="primary")
    result = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="继续"
    )
    assert agui.create_run_with_result.await_count == 0
    assert len(result.replies) == 1
    assert result.replies[0].author is AssistantGroupMessageAuthor.SYSTEM
    assert any("主助理已被移出" in notice for notice in result.notices)


@pytest.mark.asyncio
async def test_reply_as_must_be_a_current_member() -> None:
    service, _, _ = _service()
    group = await service.create_assistant_group("t", "u", name="项目群")
    with pytest.raises(ConflictError):
        await service.set_assistant_group_reply_as(
            "t", "u", group.group_id, reply_as="specialist_missing"
        )


@pytest.mark.asyncio
async def test_group_reaction_toggles_and_rejects_unknown_emoji() -> None:
    service, repo, _ = _service()
    group = await service.create_assistant_group("t", "u", name="演示群")
    result = await service.post_assistant_group_message("t", "u", group.group_id, text="你好")
    message_id = result.message.message_id

    assert await service.toggle_assistant_group_reaction(
        "t", "u", group.group_id, message_id, emoji="👍"
    ) == ("👍",)
    assert repo.reactions[(message_id, "👍")] == ["👍"]
    assert await service.toggle_assistant_group_reaction(
        "t", "u", group.group_id, message_id, emoji="👍"
    ) == ()
    with pytest.raises(ConflictError):
        await service.toggle_assistant_group_reaction(
            "t", "u", group.group_id, message_id, emoji="🚀"
        )


@pytest.mark.asyncio
async def test_mark_group_read_clears_unread() -> None:
    service, repo, _ = _service()
    group = await service.create_assistant_group("t", "u", name="演示群")
    repo.groups[group.group_id] = repo.groups[group.group_id].model_copy(
        update={"unread_count": 3}
    )
    cleared = await service.mark_assistant_group_read("t", "u", group.group_id)
    assert cleared.unread_count == 0


@pytest.mark.asyncio
async def test_group_history_includes_messages_from_before_join() -> None:
    service, repo, _ = _service()
    group = await service.create_assistant_group("t", "u", name="历史群")
    now = datetime(2026, 11, 11, tzinfo=UTC)
    repo.messages[group.group_id].append(
        AssistantGroupMessage(
            messageId="gmsg_old",
            groupId=group.group_id,
            author=AssistantGroupMessageAuthor.USER,
            authorName="我",
            text="入群前的约定",
            status="completed",
            createdAt=now,
        )
    )
    history = await service._group_history_text(
        "t", "u", group.group_id, "gmsg_new"
    )
    assert "入群前的约定" in history


@pytest.mark.asyncio
async def test_history_for_a_seated_member_is_limited_to_before_the_join() -> None:
    """继续在群里的成员不重复吃历史：只有入群前的消息才是背景。"""
    service, repo, _ = _service()
    group = await service.create_assistant_group("t", "u", name="历史群")
    primary = repo.members[group.group_id][0]
    joined = primary.joined_at
    repo.messages[group.group_id].extend(
        (
            AssistantGroupMessage(
                messageId="gmsg_before",
                groupId=group.group_id,
                author=AssistantGroupMessageAuthor.USER,
                authorName="我",
                text="入群前的约定",
                status="completed",
                createdAt=joined - timedelta(seconds=1),
            ),
            AssistantGroupMessage(
                messageId="gmsg_after",
                groupId=group.group_id,
                author=AssistantGroupMessageAuthor.USER,
                authorName="我",
                text="入群后的指令：请只回复已收到E",
                status="completed",
                createdAt=joined + timedelta(seconds=1),
            ),
        )
    )
    # 只有严格早于入群时间的消息算“入群前”。
    history = await service._group_history_text(
        "t", "u", group.group_id, "gmsg_new", member=primary
    )
    assert "入群前的约定" in history
    assert "入群后的指令" not in history


@pytest.mark.asyncio
async def test_unknown_mention_is_a_notice_and_primary_does_not_answer() -> None:
    """J：@ 一个本群和本人都没有的名字，也要给系统提示，不能由主助理代答。"""
    service, _, agui = _service()
    group = await service.create_assistant_group("t", "u", name="项目群")
    agui.create_run_with_result.reset_mock()
    result = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="【J】@编码协同 请确认你不在本群"
    )
    assert agui.create_run_with_result.await_count == 0
    assert any("@编码协同 不在本群" in notice for notice in result.notices)
    assert len(result.replies) == 1
    assert result.replies[0].author is AssistantGroupMessageAuthor.SYSTEM
    assert "@编码协同 不在本群" in result.replies[0].text


@pytest.mark.asyncio
async def test_unknown_mention_beside_a_member_still_leaves_a_notice() -> None:
    specialist = _specialist("specialist_a", "资料 Bot")
    service, _, agui = _service([specialist])
    group = await service.create_assistant_group("t", "u", name="项目群")
    await service.add_assistant_group_member(
        "t", "u", group.group_id, specialist_id=specialist.specialist_id
    )
    result = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="@资料 Bot @编码协同 一起看看"
    )
    assert agui.create_run_with_result.await_count == 1
    assert any("@编码协同 不在本群" in notice for notice in result.notices)
    notices = [
        item for item in result.replies
        if item.author is AssistantGroupMessageAuthor.SYSTEM
    ]
    assert len(notices) == 1 and "@编码协同 不在本群" in notices[0].text


@pytest.mark.asyncio
async def test_email_at_sign_is_not_mistaken_for_a_mention() -> None:
    service, _, agui = _service()
    group = await service.create_assistant_group("t", "u", name="项目群")
    agui.create_run_with_result.reset_mock()
    result = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="发到 ops@example.com 就行"
    )
    assert result.notices == ()
    assert agui.create_run_with_result.await_count == 1  # 未 @，仍由主助理接话


@pytest.mark.asyncio
async def test_continuing_member_prompt_does_not_replay_earlier_instructions() -> None:
    """I：上一条消息的“只回复已收到E”不能进入下一条的提示词。"""
    service, _, agui = _service()
    group = await service.create_assistant_group("t", "u", name="项目群")
    await service.post_assistant_group_message(
        "t", "u", group.group_id, text="【E】请只回复「已收到E」"
    )
    agui.create_run_with_result.reset_mock()
    await service.post_assistant_group_message(
        "t", "u", group.group_id, text="【I】请只回复「已收到I」"
    )
    content = agui.create_run_with_result.await_args.kwargs["request"].messages[0].content
    assert "已收到E" not in content
    assert "现在要回复的消息" in content
    assert "已收到I" in content


def _run(run_id: str, status: RunStatus, idempotency_key: str):
    return SimpleNamespace(
        run_id=run_id, status=status, idempotency_key=idempotency_key
    )


@pytest.mark.asyncio
async def test_completed_reply_without_text_is_retried_once_in_place() -> None:
    service, repo, agui = _service()
    group = await service.create_assistant_group("t", "u", name="项目群")
    posted = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="收到E"
    )
    original = posted.replies[0]

    async def get_run(tenant_id, run_id):
        if run_id.endswith("-retry"):
            return _run(run_id, RunStatus.RUNNING, "group-retry")
        return _run(run_id, RunStatus.SUCCEEDED, "group-original")

    service._runs.get = AsyncMock(side_effect=get_run)
    service._events.list_after = AsyncMock(return_value=[])
    agui.create_run_with_result.reset_mock()

    messages = await service.list_assistant_group_messages("t", "u", group.group_id)
    retried = next(item for item in messages if item.message_id == original.message_id)
    assert retried.status == "running"
    assert retried.run_id is not None and retried.run_id.endswith("-retry")
    request = agui.create_run_with_result.await_args.kwargs["request"]
    assert request.run_id.endswith("-retry")
    assert "没有产生任何文字" in request.messages[0].content
    assert "不要只思考不输出" in request.messages[0].content
    assert "No response requested" in request.messages[0].content
    assert repo.messages[group.group_id][-1].run_id == retried.run_id
    assert len(repo.messages[group.group_id]) == 2  # 仍是同一个用户消息+同一条占位

    # 第二次轮询只看重试运行，不会继续造运行。
    agui.create_run_with_result.reset_mock()
    await service.list_assistant_group_messages("t", "u", group.group_id)
    assert agui.create_run_with_result.await_count == 0


@pytest.mark.asyncio
async def test_empty_reply_is_retried_at_most_twice() -> None:
    """模型只思考不输出时会重问；最多两次，仍为空则保留兜底。"""
    service, repo, agui = _service()
    group = await service.create_assistant_group("t", "u", name="项目群")
    posted = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="收到E"
    )
    original = posted.replies[0]
    base = f"group-{original.message_id}"
    service._events.list_after = AsyncMock(return_value=[])
    runs: dict[str, object] = {
        original.run_id: _run(original.run_id, RunStatus.SUCCEEDED, base)
    }

    async def get_run(tenant_id, run_id):
        return runs[run_id]

    service._runs.get = AsyncMock(side_effect=get_run)
    agui.create_run_with_result.reset_mock()

    messages = await service.list_assistant_group_messages("t", "u", group.group_id)
    first = next(item for item in messages if item.message_id == original.message_id)
    assert first.run_id is not None and first.run_id.endswith("-retry")
    assert agui.create_run_with_result.await_args.kwargs["request"].run_id == f"{base}-retry"
    runs[first.run_id] = _run(first.run_id, RunStatus.SUCCEEDED, f"{base}-retry")

    agui.create_run_with_result.reset_mock()
    messages = await service.list_assistant_group_messages("t", "u", group.group_id)
    second = next(item for item in messages if item.message_id == original.message_id)
    assert second.run_id is not None and second.run_id.endswith("-retry2")
    assert (
        agui.create_run_with_result.await_args.kwargs["request"].run_id
        == f"{base}-retry2"
    )
    runs[second.run_id] = _run(second.run_id, RunStatus.SUCCEEDED, f"{base}-retry2")

    agui.create_run_with_result.reset_mock()
    await service.list_assistant_group_messages("t", "u", group.group_id)
    assert agui.create_run_with_result.await_count == 0


@pytest.mark.asyncio
async def test_empty_reply_after_a_tool_is_not_retried() -> None:
    """重问可能重复执行工具副作用：已经调用过工具的空结果保留原样。"""
    service, _, agui = _service()
    group = await service.create_assistant_group("t", "u", name="项目群")
    posted = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="收到E"
    )
    original = posted.replies[0]
    service._runs.get = AsyncMock(
        return_value=_run(original.run_id, RunStatus.SUCCEEDED, "group-original")
    )
    service._events.list_after = AsyncMock(
        return_value=[SimpleNamespace(type="tool.request")]
    )
    agui.create_run_with_result.reset_mock()
    messages = await service.list_assistant_group_messages("t", "u", group.group_id)
    assert agui.create_run_with_result.await_count == 0
    assert any(item.text == "这次没有返回文字。" for item in messages)


@pytest.mark.asyncio
async def test_stale_empty_reply_is_not_revived() -> None:
    service, repo, agui = _service()
    group = await service.create_assistant_group("t", "u", name="项目群")
    posted = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="收到E"
    )
    original = posted.replies[0]
    repo.messages[group.group_id][-1] = repo.messages[group.group_id][-1].model_copy(
        update={"created_at": datetime(2026, 11, 11, tzinfo=UTC)}
    )
    service._runs.get = AsyncMock(
        return_value=_run(original.run_id, RunStatus.SUCCEEDED, "group-original")
    )
    service._events.list_after = AsyncMock(return_value=[])
    agui.create_run_with_result.reset_mock()
    messages = await service.list_assistant_group_messages("t", "u", group.group_id)
    assert agui.create_run_with_result.await_count == 0
    assert any(item.text == "这次没有返回文字。" for item in messages)


# ---- 引用回复：发送校验、快照投影与历史还原 ----


def _settle_runs(service) -> None:
    """历史里只看引用快照：把回复运行固定成“已结束、没有正文”。"""
    service._runs.get = AsyncMock(
        return_value=SimpleNamespace(
            run_id="run-settled",
            status=RunStatus.FAILED,
            idempotency_key="group-original",
        )
    )
    service._events.list_after = AsyncMock(return_value=[])


@pytest.mark.asyncio
async def test_sending_a_quote_returns_the_snapshot() -> None:
    service, repo, _ = _service()
    group = await service.create_assistant_group("t", "u", name="项目群")
    first = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="原始消息"
    )
    result = await service.post_assistant_group_message(
        "t",
        "u",
        group.group_id,
        text="引用一下",
        reply_to_message_id=first.message.message_id,
    )

    quote = result.message.reply_to
    assert quote is not None
    assert quote.message_id == first.message.message_id
    assert quote.author is AssistantGroupMessageAuthor.USER
    assert quote.author_name == "我"
    assert quote.text == "原始消息"
    assert result.message.reply_to_message_id == first.message.message_id
    stored = next(
        item for item in repo.messages[group.group_id]
        if item.message_id == result.message.message_id
    )
    assert stored.reply_to_message_id == first.message.message_id
    payload = result.message.model_dump(mode="json", by_alias=True)
    assert payload["replyTo"] == {
        "messageId": first.message.message_id,
        "author": "user",
        "authorName": "我",
        "text": "原始消息",
    }


@pytest.mark.asyncio
async def test_history_carries_the_quote_but_not_bot_reply_links() -> None:
    service, _, _ = _service()
    group = await service.create_assistant_group("t", "u", name="项目群")
    first = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="原始消息"
    )
    quoted = await service.post_assistant_group_message(
        "t",
        "u",
        group.group_id,
        text="引用一下",
        reply_to_message_id=first.message.message_id,
    )
    _settle_runs(service)

    messages = await service.list_assistant_group_messages("t", "u", group.group_id)
    restored = next(
        item for item in messages if item.message_id == quoted.message.message_id
    )
    assert restored.reply_to is not None
    assert restored.reply_to.message_id == first.message.message_id
    assert restored.reply_to.text == "原始消息"
    # 机器人回复的 reply_to 只是自动回链，不该显示成引用条。
    bots = [
        item for item in messages
        if item.author is AssistantGroupMessageAuthor.PRIMARY
    ]
    assert bots
    assert all(item.reply_to is None for item in bots)
    assert all(item.reply_to_message_id for item in bots)


@pytest.mark.asyncio
async def test_quote_from_another_group_is_not_found() -> None:
    service, repo, _ = _service()
    left = await service.create_assistant_group("t", "u", name="左群")
    right = await service.create_assistant_group("t", "u", name="右群")
    first = await service.post_assistant_group_message(
        "t", "u", left.group_id, text="只在左群"
    )
    with pytest.raises(NotFoundError, match="quoted message not found"):
        await service.post_assistant_group_message(
            "t",
            "u",
            right.group_id,
            text="跨群引用",
            reply_to_message_id=first.message.message_id,
        )
    # 校验不通过时不写消息、也不触发回复。
    assert all("跨群引用" != item.text for item in repo.messages[right.group_id])


@pytest.mark.asyncio
async def test_quote_of_a_missing_message_is_not_found() -> None:
    service, _, _ = _service()
    group = await service.create_assistant_group("t", "u", name="项目群")
    with pytest.raises(NotFoundError, match="quoted message not found"):
        await service.post_assistant_group_message(
            "t", "u", group.group_id, text="引用不存在的",
            reply_to_message_id="gmsg_missing",
        )


@pytest.mark.asyncio
async def test_quote_of_a_streaming_placeholder_is_refused() -> None:
    service, _, _ = _service()
    group = await service.create_assistant_group("t", "u", name="项目群")
    posted = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="开始回答"
    )
    reply = posted.replies[0]
    assert reply.status == "running"
    with pytest.raises(ConflictError, match="not ready"):
        await service.post_assistant_group_message(
            "t", "u", group.group_id, text="引用运行中的",
            reply_to_message_id=reply.message_id,
        )


@pytest.mark.asyncio
async def test_quote_text_is_truncated_to_200_characters() -> None:
    service, _, _ = _service()
    group = await service.create_assistant_group("t", "u", name="项目群")
    first = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="长" * 260
    )
    result = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="引用长文",
        reply_to_message_id=first.message.message_id,
    )
    assert len(result.message.reply_to.text) == 200


@pytest.mark.asyncio
async def test_history_omits_the_quote_once_the_target_is_gone() -> None:
    service, repo, _ = _service()
    group = await service.create_assistant_group("t", "u", name="项目群")
    first = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="原始消息"
    )
    quoted = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="引用一下",
        reply_to_message_id=first.message.message_id,
    )
    # 群消息没有删除接口；这里模拟目标行被外部清理，读取必须降级而不是报错。
    repo.messages[group.group_id] = [
        item for item in repo.messages[group.group_id]
        if item.message_id != first.message.message_id
    ]
    _settle_runs(service)

    messages = await service.list_assistant_group_messages("t", "u", group.group_id)
    restored = next(
        item for item in messages if item.message_id == quoted.message.message_id
    )
    assert restored.reply_to is None
    assert restored.reply_to_message_id == first.message.message_id


@pytest.mark.asyncio
async def test_no_quote_keeps_the_old_payload_shape() -> None:
    service, _, _ = _service()
    group = await service.create_assistant_group("t", "u", name="项目群")
    result = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="没有引用"
    )
    payload = result.message.model_dump(mode="json", by_alias=True)
    assert "replyTo" not in payload
    assert payload["replyToMessageId"] is None


@pytest.mark.asyncio
async def test_idempotent_replay_keeps_the_quote_snapshot() -> None:
    service, _, _ = _service()
    group = await service.create_assistant_group("t", "u", name="项目群")
    first = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="原始消息"
    )
    sent = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="引用一下",
        client_message_id="client-1",
        reply_to_message_id=first.message.message_id,
    )
    _settle_runs(service)
    again = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="引用一下",
        client_message_id="client-1",
        reply_to_message_id=first.message.message_id,
    )
    assert again.message.message_id == sent.message.message_id
    assert again.message.reply_to is not None
    assert again.message.reply_to.text == "原始消息"


@pytest.mark.asyncio
async def test_quoting_a_bot_reply_hydrates_its_text() -> None:
    """机器人回复的正文不在库里：引用快照必须来自运行事件，而不是空串。"""
    service, _, _ = _service()
    group = await service.create_assistant_group("t", "u", name="项目群")
    posted = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="问一句"
    )
    reply = posted.replies[0]
    service._runs.get = AsyncMock(
        return_value=_run(reply.run_id, RunStatus.SUCCEEDED, "group-original")
    )
    service._events.list_after = AsyncMock(
        return_value=[
            SimpleNamespace(type="message.delta", payload={"text": "机器人原话"})
        ]
    )

    result = await service.post_assistant_group_message(
        "t",
        "u",
        group.group_id,
        text="引用机器人",
        reply_to_message_id=reply.message_id,
    )
    assert result.message.reply_to is not None
    assert result.message.reply_to.author is AssistantGroupMessageAuthor.PRIMARY
    assert result.message.reply_to.text == "机器人原话"


# --- 全角标点保留：落库侧不做 NFKC -----------------------------------------


@pytest.mark.asyncio
async def test_group_message_keeps_full_width_punctuation_and_markdown() -> None:
    """消息正文按原文落库：全角标点与 Markdown 结构不得被 NFKC 改写。"""
    service, repo, _ = _service()
    group = await service.create_assistant_group("t", "u", name="项目群")
    body = (
        "版本说明：一期已验收，二期待定！还有「引用」与《规范》（附注）？\n\n"
        "# 一级标题\n\n"
        "- 列表项 A；列表项 B。\n\n"
        "| 列A | 列B |\n| --- | --- |\n| 1 | 2 |\n\n"
        '```python\nprint("hi：全角")\n```'
    )
    result = await service.post_assistant_group_message(
        "t", "u", group.group_id, text=body
    )
    assert result.message.text == body
    assert repo.messages[group.group_id][0].text == body

    _settle_runs(service)
    history = await service.list_assistant_group_messages("t", "u", group.group_id)
    stored = next(
        item for item in history if item.message_id == result.message.message_id
    )
    assert stored.text == body


@pytest.mark.asyncio
async def test_quote_snapshot_keeps_full_width_punctuation() -> None:
    """引用快照从存文本派生，全角标点同样不能被改写。"""
    service, _, _ = _service()
    group = await service.create_assistant_group("t", "u", name="项目群")
    original = "结论：一期「可以」上线！（附：验收单）"
    first = await service.post_assistant_group_message(
        "t", "u", group.group_id, text=original
    )
    quoted = await service.post_assistant_group_message(
        "t",
        "u",
        group.group_id,
        text="引用一下：看这条",
        reply_to_message_id=first.message.message_id,
    )
    assert quoted.message.reply_to is not None
    assert quoted.message.reply_to.text == original
    assert quoted.message.text == "引用一下：看这条"

    _settle_runs(service)
    messages = await service.list_assistant_group_messages("t", "u", group.group_id)
    restored = next(
        item for item in messages if item.message_id == quoted.message.message_id
    )
    assert restored.reply_to is not None
    assert restored.reply_to.text == original


@pytest.mark.asyncio
async def test_mention_matching_still_normalizes_full_width_names() -> None:
    """匹配侧 NFKC 必须保留：成员名含全角字母时 @ 仍能识别。"""
    specialist = _specialist("specialist_fw", "助理Ｂｏｔ")
    service, _, agui = _service([specialist])
    group = await service.create_assistant_group("t", "u", name="项目群")
    await service.add_assistant_group_member(
        "t", "u", group.group_id, specialist_id=specialist.specialist_id
    )
    result = await service.post_assistant_group_message(
        "t", "u", group.group_id, text="@助理Bot 查一下：全角"
    )
    assert len(result.replies) == 1
    assert result.replies[0].author is AssistantGroupMessageAuthor.SPECIALIST
    assert agui.create_run_with_result.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["", "   \n\t ", "含零字节\x00的消息", "长" * 8001])
async def test_invalid_group_message_text_is_still_refused(text: str) -> None:
    service, _, _ = _service()
    group = await service.create_assistant_group("t", "u", name="项目群")
    with pytest.raises(ConflictError):
        await service.post_assistant_group_message(
            "t", "u", group.group_id, text=text
        )


@pytest.mark.asyncio
async def test_group_name_keeps_full_width_punctuation() -> None:
    service, repo, _ = _service()
    name = "项目：一期（终稿）"
    group = await service.create_assistant_group("t", "u", name=name)
    assert group.name == name
    assert repo.groups[group.group_id].name == name


@pytest.mark.asyncio
async def test_group_name_validation_matches_80_character_api_contract() -> None:
    service, _, _ = _service()
    with pytest.raises(ConflictError):
        await service.create_assistant_group("t", "u", name="   ")
    with pytest.raises(ConflictError):
        await service.create_assistant_group("t", "u", name="群" * 81)
    with pytest.raises(ConflictError):
        await service.create_assistant_group("t", "u", name="项目\x07群")
    accepted = await service.create_assistant_group("t", "u", name="群" * 80)
    assert len(accepted.name) == 80
    # 既有口径：换行等空白折叠成单个空格，不报错。
    group = await service.create_assistant_group("t", "u", name="项目\n群")
    assert group.name == "项目 群"
