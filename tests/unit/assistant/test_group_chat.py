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
        raise NotFoundError("missing")

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
async def test_removed_responder_is_not_rerouted_to_primary() -> None:
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
    assert agui.create_run_with_result.await_count == 0
    assert any("不会自动改派" in notice for notice in result.notices)
    assert result.replies[0].author is AssistantGroupMessageAuthor.SYSTEM


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
    assert repo.messages[group.group_id][-1].run_id == retried.run_id
    assert len(repo.messages[group.group_id]) == 2  # 仍是同一个用户消息+同一条占位

    # 第二次轮询只看重试运行，不会继续造运行。
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
