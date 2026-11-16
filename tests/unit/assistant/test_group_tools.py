"""Group member-management tools: only the primary seat may change the roster."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from harness.assistant.constants import GROUP_PRIMARY_MEMBER_ID, PERSONAL_ASSISTANT_THREAD
from harness.assistant.group_tools import assistant_group_management_overlay
from harness.assistant.models import (
    AssistantGroup,
    AssistantGroupMember,
    GroupMemberKind,
    Specialist,
    SpecialistStatus,
)
from harness.core.errors import NotFoundError, PermissionDeniedError
from harness.core.models import Run, RunStatus, Session
from tests.unit.assistant.test_group_manage_library import _service

NOW = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)


def _specialist(name: str = "项目助理", specialist_id: str = "specialist_pm") -> Specialist:
    return Specialist(
        specialistId=specialist_id,
        tenantId="t",
        userId="u",
        name=name,
        slug="project-bot",
        description="",
        agentName="agent",
        agentVersion="1.0",
        status=SpecialistStatus.READY,
        config={},
        createdAt=NOW,
        updatedAt=NOW,
    )


def _member(
    member_id: str, kind: GroupMemberKind, name: str, specialist_id: str | None = None
) -> AssistantGroupMember:
    return AssistantGroupMember(
        memberId=member_id,
        kind=kind,
        name=name,
        specialistId=specialist_id,
        joinedAt=NOW,
    )


def _group(members: tuple[AssistantGroupMember, ...] | None = None) -> AssistantGroup:
    roster = (
        members
        if members is not None
        else (
            _member(
                GROUP_PRIMARY_MEMBER_ID, GroupMemberKind.PRIMARY, "主助理", "specialist_primary"
            ),
            _member("specialist_pm", GroupMemberKind.SPECIALIST, "项目助理", "specialist_pm"),
        )
    )
    return AssistantGroup(
        groupId="group_1",
        name="测试群",
        creatorUserId="u",
        primaryPresent=any(item.kind is GroupMemberKind.PRIMARY for item in roster),
        members=roster,
        createdAt=NOW,
        updatedAt=NOW,
    )


class FakeAssistant:
    """Roster mutations mirror AssistantService's contract; no database involved."""

    def __init__(self, group: AssistantGroup | None, specialists: list[Specialist] | None = None):
        self.group = group
        self.specialists = specialists if specialists is not None else [_specialist()]
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.repository = SimpleNamespace(
            get_specialist=self._get_specialist,
            list_specialists=self._list_specialists,
        )

    async def _get_specialist(self, tenant_id: str, user_id: str, specialist_id: str):
        for item in self.specialists:
            if item.specialist_id == specialist_id:
                return item
        raise NotFoundError("missing")

    async def _list_specialists(self, tenant_id: str, user_id: str, **_: Any):
        return list(self.specialists)

    async def get_assistant_group(self, tenant_id: str, user_id: str, group_id: str):
        if self.group is None or group_id != self.group.group_id:
            raise NotFoundError("群不存在或无权查看")
        return self.group

    async def add_assistant_group_member(
        self, tenant_id: str, user_id: str, group_id: str, *, specialist_id: str
    ) -> AssistantGroup:
        self.calls.append(("add", {"specialist_id": specialist_id}))
        specialist = await self._get_specialist(tenant_id, user_id, specialist_id)
        member = _member(specialist_id, GroupMemberKind.SPECIALIST, specialist.name, specialist_id)
        members = tuple(item for item in self.group.members if item.member_id != specialist_id) + (
            member,
        )
        self.group = self.group.model_copy(update={"members": members})
        return self.group

    async def remove_assistant_group_member(
        self, tenant_id: str, user_id: str, group_id: str, *, member_id: str
    ) -> AssistantGroup:
        if member_id.startswith("user:"):
            raise AssertionError("service must reject the creator before this call")
        self.calls.append(("remove", {"member_id": member_id}))
        members = tuple(item for item in self.group.members if item.member_id != member_id)
        self.group = self.group.model_copy(
            update={
                "members": members,
                "primaryPresent": any(item.kind is GroupMemberKind.PRIMARY for item in members),
            }
        )
        return self.group

    async def set_assistant_group_reply_as(
        self, tenant_id: str, user_id: str, group_id: str, *, reply_as: str | None
    ) -> AssistantGroup:
        self.calls.append(("reply_as", {"reply_as": reply_as}))
        self.group = self.group.model_copy(update={"reply_as": reply_as})
        return self.group


class FakeEvents:
    def __init__(self) -> None:
        self.appended: list[dict[str, Any]] = []

    async def append(self, **kwargs: Any) -> None:
        self.appended.append(kwargs)


async def _overlay(
    assistant: FakeAssistant,
    member_id: str = GROUP_PRIMARY_MEMBER_ID,
    *,
    context_member_id: str | None = None,
) -> tuple[dict[str, Any], FakeEvents]:
    run = Run(
        run_id="group-gmsg_1",
        session_id="assistant_group_group_1_primary",
        tenant_id="t",
        status=RunStatus.RUNNING,
        idempotency_key="k",
        created_at=NOW,
        updated_at=NOW,
        input={"group_context": {"groupId": "group_1", "memberId": member_id}},
    )
    session = Session(
        session_id=run.session_id,
        tenant_id="t",
        user_id="u",
        agent_name="kai-personal-assistant",
        agent_version="1",
        created_at=NOW,
        environment="production",
        runtime_type="claude-agent-sdk",
    )
    events = FakeEvents()
    overlay = await assistant_group_management_overlay(
        assistant,  # type: ignore[arg-type]
        events,  # type: ignore[arg-type]
        run,
        session,
        group_id="group_1",
        member_id=member_id,
    )
    if context_member_id is not None:
        run.input["group_context"]["memberId"] = context_member_id
    return {tool.name: tool.handler for tool in overlay.tools}, events


def _payload(result: dict[str, Any]) -> dict[str, Any]:
    return json.loads(result["content"][0]["text"])


@pytest.mark.asyncio
async def test_primary_lists_members_and_audits_the_call():
    assistant = FakeAssistant(_group())
    tools, events = await _overlay(assistant)

    result = await tools["assistant_group_members"]({})

    payload = _payload(result)
    assert payload["status"] == "ok"
    assert {item["memberId"] for item in payload["members"]} == {
        GROUP_PRIMARY_MEMBER_ID,
        "specialist_pm",
    }
    assert events.appended[0]["event_type"] == "assistant.group.tool"
    assert events.appended[0]["payload"]["status"] == "succeeded"
    assert events.appended[0]["payload"]["actorMemberId"] == GROUP_PRIMARY_MEMBER_ID


@pytest.mark.asyncio
async def test_primary_adds_member_by_name():
    primary_only = _group(
        members=(
            _member(
                GROUP_PRIMARY_MEMBER_ID, GroupMemberKind.PRIMARY, "主助理", "specialist_primary"
            ),
        )
    )
    assistant = FakeAssistant(primary_only, specialists=[_specialist()])
    tools, events = await _overlay(assistant)

    result = await tools["assistant_group_add_member"]({"specialist": "项目助理"})

    payload = _payload(result)
    assert payload["status"] == "ok"
    assert payload["changed"] is True
    assert "specialist_pm" in {item["memberId"] for item in payload["members"]}
    assert assistant.calls == [("add", {"specialist_id": "specialist_pm"})]
    assert events.appended[-1]["payload"]["operation"] == "add_member"


@pytest.mark.asyncio
async def test_primary_removes_member_and_cannot_remove_the_creator():
    assistant = FakeAssistant(_group())
    tools, _ = await _overlay(assistant)

    result = await tools["assistant_group_remove_member"]({"member": "项目助理"})
    assert _payload(result)["status"] == "ok"
    assert assistant.calls == [("remove", {"member_id": "specialist_pm"})]

    creator = await tools["assistant_group_remove_member"]({"member": "创建者"})
    creator_payload = _payload(creator)
    assert creator_payload["status"] == "failed"
    assert "创建者" in creator_payload["error"]


@pytest.mark.asyncio
async def test_primary_sets_reply_as():
    assistant = FakeAssistant(_group())
    tools, _ = await _overlay(assistant)

    result = await tools["assistant_group_set_reply_as"]({"member": "项目助理"})
    payload = _payload(result)
    assert payload["status"] == "ok"
    assert assistant.calls == [("reply_as", {"reply_as": "specialist_pm"})]

    back = await tools["assistant_group_set_reply_as"]({"member": None})
    assert _payload(back)["status"] == "ok"
    assert assistant.calls[-1] == ("reply_as", {"reply_as": None})


@pytest.mark.asyncio
async def test_sub_assistant_is_denied_explicitly_and_changes_nothing():
    assistant = FakeAssistant(_group())
    tools, events = await _overlay(assistant, context_member_id="specialist_pm")

    result = await tools["assistant_group_remove_member"]({"member": "项目助理"})

    payload = _payload(result)
    assert result["isError"] is True
    assert payload["status"] == "failed"
    assert "主助理" in payload["error"]
    assert assistant.calls == []
    assert events.appended[-1]["payload"]["status"] == "failed"
    assert events.appended[-1]["payload"]["actorMemberId"] == "primary"


@pytest.mark.asyncio
async def test_missing_group_and_missing_member_are_distinct_errors():
    tools, _ = await _overlay(FakeAssistant(None))
    missing_group = await tools["assistant_group_members"]({})
    assert _payload(missing_group)["status"] == "failed"
    assert "群不存在" in _payload(missing_group)["error"]

    tools, _ = await _overlay(FakeAssistant(_group()))
    missing_member = await tools["assistant_group_remove_member"]({"member": "不存在的人"})
    assert _payload(missing_member)["status"] == "failed"
    assert "不存在" in _payload(missing_member)["error"]


@pytest.mark.asyncio
async def test_unknown_specialist_names_the_listing_tool():
    assistant = FakeAssistant(_group(), specialists=[])
    tools, _ = await _overlay(assistant)

    result = await tools["assistant_group_add_member"]({"specialist": "不存在"})

    payload = _payload(result)
    assert payload["status"] == "failed"
    assert "assistant_group_members" in payload["error"]
    assert assistant.calls == []


def admin_identity(*, direct=False, member_id="primary", author="user", **overrides):
    context = {
        "groupId": "group_a",
        "memberId": member_id,
        "triggerMessageId": "gmsg_user",
        "triggerAuthor": author,
    }
    run = Run(
        run_id="admin-run",
        session_id="admin-session",
        tenant_id="t",
        status=RunStatus.RUNNING,
        idempotency_key="admin",
        created_at=NOW,
        updated_at=NOW,
        input={
            "conversation_surface": "assistant",
            **(
                {}
                if direct
                else {
                    "group_context": context,
                }
            ),
        },
        **overrides,
    )
    session = Session(
        session_id=run.session_id,
        tenant_id="t",
        user_id="u",
        agent_name="kai-personal-assistant",
        agent_version="1",
        created_at=NOW,
        environment="production",
        runtime_type="claude-agent-sdk",
    )
    return run, session


async def admin_overlay(service, run, session, events=None):
    events = events or FakeEvents()
    context = run.input.get("group_context", {})
    overlay = await assistant_group_management_overlay(
        service,
        events,
        run,
        session,
        group_id=context.get("groupId"),
        member_id=context.get("memberId", "primary"),
    )
    return {tool.name: tool.handler for tool in overlay.tools}, events


@pytest.mark.asyncio
@pytest.mark.parametrize("direct", [False, True], ids=["group-primary", "one-to-one-primary"])
async def test_admin_creates_for_session_user_with_primary_only_and_sidebar_visibility(direct):
    service, repo, agui = _service()
    repo.seed_group()
    agui.get_thread_record_for_session.return_value = SimpleNamespace(
        thread_id=PERSONAL_ASSISTANT_THREAD,
    )
    run, session = admin_identity(direct=direct)
    tools, events = await admin_overlay(service, run, session)
    result = _payload(await tools["assistant_group_create"]({"name": "新群：项目"}))
    assert result["status"] == "ok"
    group_id = result["groupId"]
    assert repo.groups[group_id].creator_user_id == session.user_id
    assert [(m.member_id, m.kind) for m in repo.members[group_id]] == [
        ("primary", GroupMemberKind.PRIMARY),
    ]
    # The creator is a virtual service-view member; only primary is stored.
    repo.list_groups = AsyncMock(return_value=[repo.groups[group_id]])
    listed = await service.list_assistant_groups("t", session.user_id)
    assert listed[0].group_id == group_id
    assert listed[0].creator_user_id == session.user_id
    repo.list_groups.assert_awaited_once_with("t", "u")
    assert events.appended[-1]["payload"] == {
        "operation": "create_group",
        "groupId": group_id,
        "actorMemberId": "primary",
        "actorUserId": "u",
        "runId": run.run_id,
        "status": "succeeded",
        "newName": "新群：项目",
    }
    if direct:
        assert set(tools) == {"assistant_group_create"}


ADMIN_CASES = [
    ("assistant_group_create", {"name": "新群"}),
    ("assistant_group_update", {"name": "新名字", "description": "新描述"}),
    ("assistant_group_dissolve", {"confirmName": "项目群"}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "arguments"), ADMIN_CASES)
async def test_each_admin_tool_denies_specialist_with_403_and_no_writes(tool, arguments):
    service, repo, _ = _service()
    repo.seed_group()
    run, session = admin_identity()
    tools, events = await admin_overlay(service, run, session)
    # A cached primary handler must still reject a now-specialist run context.
    run.input["group_context"]["memberId"] = "specialist_pm"
    before = dict(repo.groups)
    result = await tools[tool](arguments)
    assert result["isError"] is True
    assert _payload(result)["statusCode"] == 403
    assert repo.groups == before
    assert len(events.appended) == 1
    assert events.appended[0]["payload"]["status"] == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "arguments"), ADMIN_CASES)
@pytest.mark.parametrize("field", ["groupId", "memberId", "triggerAuthor", "userId"])
async def test_each_admin_tool_rejects_model_supplied_group_or_identity(tool, arguments, field):
    service, repo, _ = _service()
    repo.seed_group()
    repo.seed_group("other_group")
    tools, _ = await admin_overlay(service, *admin_identity())
    result = await tools[tool]({**arguments, field: "other_group"})
    assert result["isError"] is True
    assert _payload(result)["statusCode"] == 400
    assert repo.groups["group_a"].name == "项目群"
    assert repo.groups["other_group"].name == "项目群"
    assert not repo.dissolved
    assert len(repo.groups) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "arguments"), ADMIN_CASES)
async def test_each_admin_tool_cannot_access_other_users_group(tool, arguments):
    service, repo, _ = _service()
    repo.seed_group(user_id="other-user")
    tools, _ = await admin_overlay(service, *admin_identity())
    result = await tools[tool](arguments)
    assert _payload(result)["statusCode"] == 404
    assert repo.groups["group_a"].creator_user_id == "other-user"


@pytest.mark.asyncio
async def test_update_only_explicit_fields_and_empty_patch_does_not_write():
    service, repo, _ = _service()
    repo.seed_group()
    repo.update_group = AsyncMock(wraps=repo.update_group)
    tools, events = await admin_overlay(service, *admin_identity())
    result = _payload(await tools["assistant_group_update"]({}))
    assert result["name"] == "项目群"
    repo.update_group.assert_not_awaited()
    result = _payload(await tools["assistant_group_update"]({"name": "新群：Ａ"}))
    assert result["name"] == "新群：Ａ"
    assert result["description"] is None
    assert repo.update_group.await_args.kwargs == {"fields": {"name": "新群：Ａ"}}
    result = _payload(await tools["assistant_group_update"]({"description": "描述：保留"}))
    assert result["name"] == "新群：Ａ"
    assert result["description"] == "描述：保留"
    assert events.appended[-1]["payload"]["oldName"] == "新群：Ａ"
    assert events.appended[-1]["payload"]["newName"] == "新群：Ａ"
    result = _payload(await tools["assistant_group_update"]({"description": None}))
    assert result["description"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"name": ""},
        {"name": " "},
        {"name": "字" * 81},
        {"name": None},
        {"description": "字" * 501},
        {"description": 1},
        {"description": "坏\x07字"},
    ],
)
async def test_update_rejects_invalid_name_and_description_without_writes(changes):
    service, repo, _ = _service()
    repo.seed_group()
    repo.update_group = AsyncMock(wraps=repo.update_group)
    tools, events = await admin_overlay(service, *admin_identity())
    result = await tools["assistant_group_update"](changes)
    assert result["isError"] is True
    repo.update_group.assert_not_awaited()
    assert len(events.appended) == 1
    assert events.appended[0]["payload"]["status"] == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", [None, "", " ", "字" * 81, 12])
async def test_create_validates_name_without_creating_group(name):
    service, repo, _ = _service()
    repo.seed_group()
    tools, events = await admin_overlay(service, *admin_identity())
    result = await tools["assistant_group_create"]({"name": name})
    assert result["isError"] is True
    assert list(repo.groups) == ["group_a"]
    assert len(events.appended) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("author", ["specialist", "primary", None])
async def test_dissolve_rejects_non_user_trigger_and_keeps_group(author):
    service, repo, _ = _service()
    repo.seed_group()
    tools, events = await admin_overlay(service, *admin_identity(author=author))
    result = _payload(await tools["assistant_group_dissolve"]({"confirmName": "项目群"}))
    assert result["statusCode"] == 403
    assert result["error"] == "解散群需要你本人提出"
    assert "group_a" in repo.groups
    assert len(events.appended) == 1


@pytest.mark.asyncio
async def test_dissolve_requires_trigger_message_id():
    service, repo, _ = _service()
    repo.seed_group()
    run, session = admin_identity()
    del run.input["group_context"]["triggerMessageId"]
    tools, _ = await admin_overlay(service, run, session)
    result = _payload(await tools["assistant_group_dissolve"]({"confirmName": "项目群"}))
    assert result["statusCode"] == 403
    assert "group_a" in repo.groups


@pytest.mark.asyncio
async def test_dissolve_wrong_name_then_user_confirmed_nfkc_name_deletes_and_reads_404():
    service, repo, _ = _service()
    repo.seed_group(name="项目群Ａ")
    tools, events = await admin_overlay(service, *admin_identity())
    wrong = _payload(await tools["assistant_group_dissolve"]({"confirmName": "错群"}))
    assert wrong["error"] == "群名不一致，未解散；当前群名是『项目群Ａ』"
    assert "group_a" in repo.groups
    # NFKC only; extra whitespace is not an exact confirmation.
    wrong = _payload(await tools["assistant_group_dissolve"]({"confirmName": " 项目群A"}))
    assert wrong["status"] == "failed"
    correct = _payload(await tools["assistant_group_dissolve"]({"confirmName": "项目群A"}))
    assert correct["dissolved"] is True
    assert correct["groupId"] == "group_a"
    with pytest.raises(NotFoundError):
        await service.get_assistant_group("t", "u", "group_a")
    assert [e["payload"]["status"] for e in events.appended] == [
        "failed",
        "failed",
        "succeeded",
    ]


@pytest.mark.asyncio
async def test_dissolve_detects_rename_between_confirmation_and_delete():
    service, repo, _ = _service()
    repo.seed_group()
    original_delete = repo.delete_group

    async def concurrently_renamed(*args, **kwargs):
        repo.groups["group_a"] = repo.groups["group_a"].model_copy(update={"name": "新群名"})
        await original_delete(*args, **kwargs)

    repo.delete_group = concurrently_renamed
    tools, _ = await admin_overlay(service, *admin_identity())
    result = _payload(await tools["assistant_group_dissolve"]({"confirmName": "项目群"}))
    assert result["status"] == "failed"
    assert "新群名" in result["error"]
    assert "group_a" in repo.groups


@pytest.mark.asyncio
async def test_failed_audit_does_not_turn_successful_update_into_failure():
    service, repo, _ = _service()
    repo.seed_group()
    events = SimpleNamespace(append=AsyncMock(side_effect=RuntimeError("sink unavailable")))
    tools, _ = await admin_overlay(service, *admin_identity(), events=events)
    result = _payload(await tools["assistant_group_update"]({"name": "已改名"}))
    assert result["status"] == "ok"
    assert repo.groups["group_a"].name == "已改名"
    events.append.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_input",
    [
        {"conversation_surface": "workbench"},
        {"conversation_surface": "assistant", "assistant_dispatch": True},
    ],
)
async def test_direct_creation_rejects_non_assistant_and_delegated_runs(bad_input):
    service, repo, _ = _service()
    run, session = admin_identity(direct=True)
    run = run.model_copy(update={"input": bad_input})
    with pytest.raises(PermissionDeniedError):
        await service.admin_create_assistant_group(run, session, name="不该创建")
    assert not repo.groups


@pytest.mark.asyncio
async def test_direct_creation_rejects_specialist_thread_even_with_assistant_surface():
    service, repo, agui = _service()
    agui.get_thread_record_for_session.return_value = SimpleNamespace(thread_id="specialist_pm")
    with pytest.raises(PermissionDeniedError):
        await service.admin_create_assistant_group(*admin_identity(direct=True), name="不该创建")
    assert not repo.groups


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed", [{"tenant_id": "other"}, {"session_id": "other"}, {"project": "project"}]
)
async def test_admin_rejects_mismatched_session_tenant_and_project_runs(changed):
    service, repo, _ = _service()
    repo.seed_group()
    run, session = admin_identity()
    if "project" in changed:
        changed = {"input": {**run.input, "business_project_id": changed["project"]}}
    with pytest.raises(PermissionDeniedError):
        await service.admin_update_assistant_group(
            run.model_copy(update=changed),
            session,
            changes={"name": "不该改"},
        )
    assert repo.groups["group_a"].name == "项目群"


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "arguments"), ADMIN_CASES)
async def test_each_admin_tool_denies_absent_primary(tool, arguments):
    service, repo, _ = _service()
    repo.seed_group()
    repo.members["group_a"] = []
    tools, _ = await admin_overlay(service, *admin_identity())
    result = _payload(await tools[tool](arguments))
    assert result["statusCode"] == 403
    assert "group_a" in repo.groups
    assert len(repo.groups) == 1


@pytest.mark.asyncio
async def test_specialist_overlay_exposes_no_group_tools_but_explains_primary_boundary():
    service, _, _ = _service()
    run, session = admin_identity(member_id="specialist_pm")
    overlay = await assistant_group_management_overlay(
        service,
        FakeEvents(),
        run,
        session,
        group_id="group_a",
        member_id="specialist_pm",
    )
    assert overlay.tools == ()
    assert "请让本群主助理处理" in overlay.instructions


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "arguments"), ADMIN_CASES)
async def test_service_denies_direct_specialist_admin_calls_even_without_tool_discovery(
    tool, arguments
):
    service, repo, _ = _service()
    repo.seed_group()
    run, session = admin_identity(member_id="specialist_pm")
    with pytest.raises(PermissionDeniedError):
        if tool == "assistant_group_create":
            await service.admin_create_assistant_group(run, session, name=arguments["name"])
        elif tool == "assistant_group_update":
            await service.admin_update_assistant_group(run, session, changes=arguments)
        else:
            await service.admin_dissolve_assistant_group(
                run,
                session,
                confirm_name=arguments["confirmName"],
            )
    assert repo.groups["group_a"].name == "项目群"
