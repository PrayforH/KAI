"""Group member-management tools: only the primary seat may change the roster."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from harness.assistant.constants import GROUP_PRIMARY_MEMBER_ID
from harness.assistant.group_tools import assistant_group_management_overlay
from harness.assistant.models import (
    AssistantGroup,
    AssistantGroupMember,
    GroupMemberKind,
    Specialist,
    SpecialistStatus,
)
from harness.core.errors import NotFoundError
from harness.core.models import Run, RunStatus, Session

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
    assistant: FakeAssistant, member_id: str = GROUP_PRIMARY_MEMBER_ID
) -> tuple[dict[str, Any], FakeEvents]:
    run = Run(
        run_id="group-gmsg_1",
        session_id="assistant_group_group_1_primary",
        tenant_id="t",
        status=RunStatus.RUNNING,
        idempotency_key="k",
        created_at=NOW,
        updated_at=NOW,
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
    tools, events = await _overlay(assistant, member_id="specialist_pm")

    result = await tools["assistant_group_remove_member"]({"member": "项目助理"})

    payload = _payload(result)
    assert result["isError"] is True
    assert payload["status"] == "failed"
    assert "主助理" in payload["error"]
    assert assistant.calls == []
    assert events.appended[-1]["payload"]["status"] == "failed"
    assert events.appended[-1]["payload"]["actorMemberId"] == "specialist_pm"


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
