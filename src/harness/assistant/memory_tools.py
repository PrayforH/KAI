"""Model-facing memory tools: search, correct and forget, scoped to the owner.

The memory bank already carries sources, confidence, versions and a
permission-filtered projection. These tools expose that authority to the
personal assistant on the owner's behalf — never across users, and every
mutation goes through the bank's optimistic version checks so a stale
correction is refused instead of silently overwriting.
"""

from typing import Any, Protocol

from harness.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from harness.core.models import Run, Session
from harness.runtime.platform_tools import PlatformTool, PlatformToolOverlay

INSTRUCTIONS = (
    "记忆条目带来源与版本；只有用户明确要求记住/更正/忘记某条信息时才调用。\n"
    "更正和删除都必须先 assistant_memory_search 拿到 entry_id 与 version，"
    "并用当时返回的 version 提交；版本过期说明条目已变化，需重新搜索确认。"
)


class MemoryGateway(Protocol):
    """The three memory operations the assistant needs, nothing more."""

    async def search(
        self, tenant_id: str, user_id: str, agent_name: str, query: str
    ) -> list[dict[str, Any]]: ...

    async def update(
        self,
        tenant_id: str,
        user_id: str,
        entry_id: str,
        *,
        expected_version: int,
        content: str,
    ) -> dict[str, Any]: ...

    async def delete(
        self, tenant_id: str, user_id: str, entry_id: str, *, expected_version: int
    ) -> dict[str, Any]: ...


def _text(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}]}


def assistant_memory_overlay(
    gateway: MemoryGateway, run: Run, session: Session
) -> PlatformToolOverlay:
    tenant_id, user_id = session.tenant_id, session.user_id

    async def search(arguments: dict[str, Any]) -> dict[str, Any]:
        query = str(arguments.get("query", "")).strip()
        if not 1 <= len(query) <= 4_000:
            return _text("查询需在 1–4000 字之间。")
        try:
            hits = await gateway.search(tenant_id, user_id, session.agent_name, query)
        except (ConflictError, NotFoundError, PermissionDeniedError, ValueError) as error:
            return _text(f"记忆查询失败：{error}")
        if not hits:
            return _text("没有匹配的记忆条目。")
        lines = [
            f"- {hit['entry_id']}（v{hit['version']}，来源 {hit.get('source', '未知')}，"
            f"状态 {hit.get('status', 'active')}）：{hit['content'][:200]}"
            for hit in hits[:8]
        ]
        return {"content": [{"type": "text", "text": "\n".join(lines)}]}

    async def correct(arguments: dict[str, Any]) -> dict[str, Any]:
        entry_id = str(arguments.get("entry_id", ""))
        content = str(arguments.get("content", "")).strip()
        version = arguments.get("expected_version")
        if not entry_id or not content:
            return _text("需要 entry_id 和更正后的内容。")
        if not isinstance(version, int) or version < 1:
            return _text("expected_version 必须来自 assistant_memory_search 的返回。")
        try:
            updated = await gateway.update(
                tenant_id,
                user_id,
                entry_id,
                expected_version=version,
                content=content,
            )
        except ConflictError:
            return _text("该条目已被修改，请重新搜索后再更正。")
        except (NotFoundError, PermissionDeniedError) as error:
            return _text(f"更正失败：{error}")
        return _text(
            f"已更正条目 {updated['entry_id']}（现为 v{updated['version']}）。"
            f"来源与作用域保持不变。"
        )

    async def forget(arguments: dict[str, Any]) -> dict[str, Any]:
        entry_id = str(arguments.get("entry_id", ""))
        version = arguments.get("expected_version")
        if not entry_id:
            return _text("需要 entry_id。")
        if not isinstance(version, int) or version < 1:
            return _text("expected_version 必须来自 assistant_memory_search 的返回。")
        try:
            await gateway.delete(tenant_id, user_id, entry_id, expected_version=version)
        except ConflictError:
            return _text("该条目已被修改，请重新搜索后再删除。")
        except (NotFoundError, PermissionDeniedError) as error:
            return _text(f"删除失败：{error}")
        return _text(f"已删除条目 {entry_id}。")

    return PlatformToolOverlay(
        tools=(
            PlatformTool(
                name="assistant_memory_search",
                description="按关键词搜索用户的记忆条目，返回 entry_id、版本、来源与内容摘录。",
                schema={
                    "type": "object",
                    "properties": {"query": {"type": "string", "maxLength": 4000}},
                    "required": ["query"],
                },
                handler=search,
            ),
            PlatformTool(
                name="assistant_memory_correct",
                description="更正一条记忆：以搜索返回的 version 提交新内容，来源保持不变。",
                schema={
                    "type": "object",
                    "properties": {
                        "entry_id": {"type": "string"},
                        "content": {"type": "string", "maxLength": 8000},
                        "expected_version": {"type": "integer", "minimum": 1},
                    },
                    "required": ["entry_id", "content", "expected_version"],
                },
                handler=correct,
            ),
            PlatformTool(
                name="assistant_memory_forget",
                description="删除一条记忆（同样以搜索返回的 version 提交）。",
                schema={
                    "type": "object",
                    "properties": {
                        "entry_id": {"type": "string"},
                        "expected_version": {"type": "integer", "minimum": 1},
                    },
                    "required": ["entry_id", "expected_version"],
                },
                handler=forget,
            ),
        ),
        instructions=INSTRUCTIONS,
    )


class MemoryBankGateway:
    """Adapts the memory bank into the narrow gateway; search hits stay dicts.

    ``scope`` overrides the memory namespace: a specialist's duty thread scopes
    its domain memory to the specialist instance instead of the underlying
    agent, so two duties of the same agent never share domain memory.
    """

    def __init__(self, bank: Any, *, scope: str | None = None) -> None:
        self._bank = bank
        self._scope = scope

    async def search(
        self, tenant_id: str, user_id: str, agent_name: str, query: str
    ) -> list[dict[str, Any]]:
        hits = await self._bank.search(
            tenant_id, user_id, self._scope or agent_name, query
        )
        return [
            {
                "entry_id": hit.entry.entry_id,
                "version": hit.entry.version,
                "content": hit.entry.content,
                "source": hit.entry.source_kind.value,
                "status": hit.entry.status.value,
            }
            for hit in hits
        ]

    async def update(
        self,
        tenant_id: str,
        user_id: str,
        entry_id: str,
        *,
        expected_version: int,
        content: str,
    ) -> dict[str, Any]:
        entry = await self._bank.update(
            tenant_id,
            user_id,
            entry_id,
            expected_version=expected_version,
            content=content,
            confidence=None,
        )
        return {"entry_id": entry.entry_id, "version": entry.version}

    async def delete(
        self, tenant_id: str, user_id: str, entry_id: str, *, expected_version: int
    ) -> dict[str, Any]:
        entry = await self._bank.delete(tenant_id, user_id, entry_id, expected_version)
        return {"entry_id": entry.entry_id, "version": entry.version}
