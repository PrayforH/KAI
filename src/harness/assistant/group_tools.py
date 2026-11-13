"""Member-management tools for the group coordinator (the primary seat).

Every group-member run receives these tools, but the handler is the authority
check: only the primary seat may change the roster. A sub-assistant that calls
one gets an explicit permission error instead of an unknown-tool failure, which
keeps the failure mode readable for the model and the person.

The group and seat identity come from a server-authored run input
(`group_context`); clients cannot claim a seat through forwarded props.
"""

from __future__ import annotations

import json
import logging
import unicodedata
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from harness.assistant.constants import GROUP_PRIMARY_MEMBER_ID
from harness.assistant.models import (
    AssistantGroup,
    AssistantGroupMember,
    GroupMemberKind,
    Specialist,
)
from harness.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from harness.runtime.platform_tools import PlatformTool, PlatformToolOverlay

if TYPE_CHECKING:
    from harness.application.events import EventService
    from harness.assistant.service import AssistantService
    from harness.core.models import Run, Session

logger = logging.getLogger(__name__)

GROUP_TOOL_EVENT = "assistant.group.tool"


def _result(payload: dict[str, Any], *, failed: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        **({"isError": True} if failed else {}),
    }


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip()


def _group_payload(group: AssistantGroup) -> dict[str, Any]:
    return {
        "status": "ok",
        "groupId": group.group_id,
        "name": group.name,
        "primaryPresent": group.primary_present,
        "replyAs": group.reply_as,
        "members": [
            {
                "memberId": member.member_id,
                "name": member.name,
                "kind": member.kind.value,
                "specialistId": member.specialist_id,
            }
            for member in group.members
        ],
    }


async def assistant_group_management_overlay(
    assistant: AssistantService,
    events: EventService,
    run: Run,
    session: Session,
    *,
    group_id: str,
    member_id: str,
) -> PlatformToolOverlay:
    """Tools scoped to one group seat; the primary seat is the only writer."""

    tenant_id, user_id = run.tenant_id, session.user_id

    async def _audit(
        operation: str,
        status: str,
        *,
        error: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Record who did what to which group, and the outcome.

        Audit must never turn a successful roster change into a failed tool call,
        so a broken event sink is logged rather than raised.
        """

        payload: dict[str, Any] = {
            "operation": operation,
            "groupId": group_id,
            "actorMemberId": member_id,
            "actorUserId": user_id,
            "runId": run.run_id,
            "status": status,
            **(detail or {}),
        }
        if error is not None:
            payload["error"] = error
        logger.info(
            "group tool %s %s group=%s actor=%s run=%s%s",
            operation,
            status,
            group_id,
            member_id,
            run.run_id,
            f" error={error}" if error is not None else "",
        )
        try:
            await events.append(
                tenant_id=tenant_id,
                run_id=run.run_id,
                session_id=run.session_id,
                event_type=GROUP_TOOL_EVENT,
                payload=payload,
            )
        except Exception:  # noqa: BLE001 - audit failure must not break the action
            logger.exception("could not record group tool audit for %s", operation)

    async def _run(
        operation: str,
        action: Callable[[], Awaitable[tuple[dict[str, Any], dict[str, Any]]]],
    ) -> dict[str, Any]:
        try:
            payload, detail = await action()
        except (ConflictError, NotFoundError, PermissionDeniedError, ValueError) as error:
            await _audit(operation, "failed", error=str(error))
            return _result(
                {"status": "failed", "operation": operation, "error": str(error)},
                failed=True,
            )
        await _audit(operation, "succeeded", detail=detail)
        return _result(payload)

    async def _primary_group() -> AssistantGroup:
        if member_id != GROUP_PRIMARY_MEMBER_ID:
            raise PermissionDeniedError(
                "只有本群主助理可以管理群成员；子助理无权操作，请让主助理处理。"
            )
        group = await assistant.get_assistant_group(tenant_id, user_id, group_id)
        actor = next(
            (item for item in group.members if item.member_id == GROUP_PRIMARY_MEMBER_ID),
            None,
        )
        if actor is None or actor.kind is not GroupMemberKind.PRIMARY:
            raise PermissionDeniedError("主助理当前不在本群，无法管理群成员。")
        return group

    def _match_members(group: AssistantGroup, reference: str) -> list[AssistantGroupMember]:
        wanted = _normalized(reference)
        return [
            member
            for member in group.members
            if member.member_id == reference or _normalized(member.name) == wanted
        ]

    async def _resolve_specialist(reference: str) -> Specialist:
        value = reference.strip()
        if value.startswith("specialist_"):
            try:
                return await assistant.repository.get_specialist(tenant_id, user_id, value)
            except NotFoundError:
                raise NotFoundError(f"未找到标识为「{value}」的子助理。") from None
        bots = await assistant.repository.list_specialists(tenant_id, user_id)
        wanted = _normalized(value)
        matches = [bot for bot in bots if _normalized(bot.name) == wanted]
        if not matches:
            raise NotFoundError(
                f"未找到名为「{value}」的子助理；可先用 assistant_group_members 查看现有成员。"
            )
        if len(matches) > 1:
            candidates = "、".join(bot.specialist_id for bot in matches)
            raise ConflictError(
                f"有多个名为「{value}」的子助理，请改用 specialist_id：{candidates}"
            )
        return matches[0]

    async def list_members(arguments: dict[str, Any]) -> dict[str, Any]:
        del arguments

        async def action() -> tuple[dict[str, Any], dict[str, Any]]:
            group = await _primary_group()
            return _group_payload(group), {"memberCount": len(group.members)}

        return await _run("list_members", action)

    async def add_member(arguments: dict[str, Any]) -> dict[str, Any]:
        reference = arguments.get("specialist")
        if not isinstance(reference, str) or not reference.strip():
            return _result(
                {
                    "status": "failed",
                    "operation": "add_member",
                    "error": "请提供要加入群的子助理名称或 specialist_id。",
                },
                failed=True,
            )

        async def action() -> tuple[dict[str, Any], dict[str, Any]]:
            group = await _primary_group()
            specialist = await _resolve_specialist(reference)
            already_present = any(
                item.member_id == specialist.specialist_id
                for item in group.members
                if item.status == "active"
            )
            updated = await assistant.add_assistant_group_member(
                tenant_id, user_id, group_id, specialist_id=specialist.specialist_id
            )
            payload = _group_payload(updated)
            payload["changed"] = not already_present
            payload["memberId"] = specialist.specialist_id
            return payload, {
                "targetMemberId": specialist.specialist_id,
                "changed": not already_present,
            }

        return await _run("add_member", action)

    async def remove_member(arguments: dict[str, Any]) -> dict[str, Any]:
        reference = arguments.get("member")
        if not isinstance(reference, str) or not reference.strip():
            return _result(
                {
                    "status": "failed",
                    "operation": "remove_member",
                    "error": "请提供要移出群的成员名称或 member_id。",
                },
                failed=True,
            )

        async def action() -> tuple[dict[str, Any], dict[str, Any]]:
            group = await _primary_group()
            matches = _match_members(group, reference.strip())
            if not matches:
                raise NotFoundError(f"群成员「{reference.strip()}」不存在或已被移出。")
            if len(matches) > 1:
                candidates = "、".join(item.member_id for item in matches)
                raise ConflictError(
                    f"有多个成员匹配「{reference.strip()}」，请改用 member_id：{candidates}"
                )
            target = matches[0]
            if target.kind is GroupMemberKind.CREATOR:
                raise PermissionDeniedError("创建者不能被移出群聊。")
            updated = await assistant.remove_assistant_group_member(
                tenant_id, user_id, group_id, member_id=target.member_id
            )
            payload = _group_payload(updated)
            payload["changed"] = True
            payload["memberId"] = target.member_id
            return payload, {"targetMemberId": target.member_id, "changed": True}

        return await _run("remove_member", action)

    async def set_reply_as(arguments: dict[str, Any]) -> dict[str, Any]:
        reference = arguments.get("member")

        async def action() -> tuple[dict[str, Any], dict[str, Any]]:
            group = await _primary_group()
            if reference is None or (
                isinstance(reference, str) and _normalized(reference) in {"", "primary", "主助理"}
            ):
                target: str | None = None
            elif isinstance(reference, str):
                matches = _match_members(group, reference.strip())
                if not matches:
                    raise NotFoundError(f"群成员「{reference.strip()}」不存在或已被移出。")
                if len(matches) > 1:
                    candidates = "、".join(item.member_id for item in matches)
                    raise ConflictError(
                        f"有多个成员匹配「{reference.strip()}」，请改用 member_id：{candidates}"
                    )
                target = matches[0].member_id
            else:
                raise ValueError("member 必须是成员名称、member_id 或 null（主助理）。")
            changed = group.reply_as != target
            updated = await assistant.set_assistant_group_reply_as(
                tenant_id, user_id, group_id, reply_as=target
            )
            payload = _group_payload(updated)
            payload["changed"] = changed
            return payload, {"targetMemberId": target, "changed": changed}

        return await _run("set_reply_as", action)

    primary = member_id == GROUP_PRIMARY_MEMBER_ID
    instructions = (
        "## 群成员管理\n"
        "你是本群主助理（协调者）。用户明确要求查看、添加或移出成员，或调整"
        "未 @ 时的接话人时，使用 assistant_group_* 工具；不要自行推测或批量改动。"
        "成员名重复时请用户确认，或改用 member_id；创建者不能被移出。"
        "操作后用一两句自然语言说明结果和当前成员，不复述 memberId、"
        "specialist_id 或 JSON；被拒绝时如实说明原因。"
        if primary
        else ("## 群成员管理\n你不是本群主助理，不能管理群成员；被要求时说明需要由主助理操作。")
    )
    return PlatformToolOverlay(
        tools=(
            PlatformTool(
                name="assistant_group_members",
                description="列出本群成员及其角色；用于确认名称和 member_id。仅主助理可调用。",
                schema={"type": "object", "properties": {}, "additionalProperties": False},
                handler=list_members,
            ),
            PlatformTool(
                name="assistant_group_add_member",
                description=(
                    "把本人名下的子助理加入本群；参数为子助理名称或 specialist_id。仅主助理可调用。"
                ),
                schema={
                    "type": "object",
                    "properties": {
                        "specialist": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 128,
                            "description": "子助理名称或 specialist_id",
                        }
                    },
                    "required": ["specialist"],
                    "additionalProperties": False,
                },
                handler=add_member,
            ),
            PlatformTool(
                name="assistant_group_remove_member",
                description=(
                    "把成员移出本群；参数为成员名称或 member_id，创建者不可移出。仅主助理可调用。"
                ),
                schema={
                    "type": "object",
                    "properties": {
                        "member": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 128,
                            "description": "成员名称或 member_id",
                        }
                    },
                    "required": ["member"],
                    "additionalProperties": False,
                },
                handler=remove_member,
            ),
            PlatformTool(
                name="assistant_group_set_reply_as",
                description=(
                    "设置本群未被 @ 时由谁接话；member 为主助理、成员名称、member_id 或 null。"
                    "仅主助理可调用。"
                ),
                schema={
                    "type": "object",
                    "properties": {
                        "member": {
                            "anyOf": [
                                {"type": "string", "maxLength": 128},
                                {"type": "null"},
                            ],
                            "description": "成员名称、member_id、primary 或 null（主助理）",
                        }
                    },
                    "required": ["member"],
                    "additionalProperties": False,
                },
                handler=set_reply_as,
            ),
        ),
        instructions=instructions,
    )
