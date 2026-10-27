"""Memory tool flow: scoped search, version-guarded correction and deletion."""

from datetime import UTC, datetime
from typing import Any

import pytest

from harness.assistant.memory_tools import assistant_memory_overlay
from harness.core.errors import ConflictError
from harness.core.models import Run, RunStatus, Session

NOW = datetime(2026, 10, 28, 6, 0, tzinfo=UTC)


class FakeMemoryGateway:
    """In-memory entries with the bank's optimistic version semantics."""

    def __init__(self) -> None:
        self.entries: dict[str, dict[str, Any]] = {
            "mem_1": {
                "entry_id": "mem_1",
                "version": 3,
                "content": "用户偏好深色主题",
                "source": "user_stated",
                "status": "active",
            }
        }
        self.deleted: list[str] = []

    async def search(
        self, tenant_id: str, user_id: str, agent_name: str, query: str
    ) -> list[dict[str, Any]]:
        return [
            dict(entry)
            for entry in self.entries.values()
            if any(term in entry["content"] for term in query.split())
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
        entry = self.entries[entry_id]
        if entry["version"] != expected_version:
            raise ConflictError("memory entry changed")
        entry["content"] = content
        entry["version"] += 1
        return {"entry_id": entry_id, "version": entry["version"]}

    async def delete(
        self, tenant_id: str, user_id: str, entry_id: str, *, expected_version: int
    ) -> dict[str, Any]:
        entry = self.entries[entry_id]
        if entry["version"] != expected_version:
            raise ConflictError("memory entry changed")
        self.deleted.append(entry_id)
        return {"entry_id": entry_id, "version": entry["version"]}


def overlay(gateway: FakeMemoryGateway) -> Any:
    run = Run(
        run_id="run-1",
        session_id="session-1",
        tenant_id="t",
        status=RunStatus.RUNNING,
        idempotency_key="k",
        created_at=NOW,
        updated_at=NOW,
    )
    session = Session(
        session_id="session-1",
        tenant_id="t",
        user_id="u",
        agent_name="kai-personal-assistant",
        agent_version="1",
        created_at=NOW,
        environment="production",
        runtime_type="claude-agent-sdk",
    )
    return assistant_memory_overlay(gateway, run, session)


def text_of(result: dict[str, Any]) -> str:
    return str(result["content"][0]["text"])


@pytest.mark.asyncio
async def test_search_reports_entry_id_version_and_source():
    gateway = FakeMemoryGateway()
    tools = {tool.name: tool.handler for tool in overlay(gateway).tools}
    result = await tools["assistant_memory_search"]({"query": "深色"})
    body = text_of(result)
    assert "mem_1" in body and "v3" in body and "user_stated" in body


@pytest.mark.asyncio
async def test_correction_requires_the_returned_version_and_keeps_provenance():
    gateway = FakeMemoryGateway()
    tools = {tool.name: tool.handler for tool in overlay(gateway).tools}
    stale = await tools["assistant_memory_correct"](
        {"entry_id": "mem_1", "content": "浅色主题", "expected_version": 2}
    )
    assert "重新搜索" in text_of(stale)
    ok = await tools["assistant_memory_correct"](
        {"entry_id": "mem_1", "content": "用户偏好浅色主题", "expected_version": 3}
    )
    assert "v4" in text_of(ok)
    assert gateway.entries["mem_1"]["source"] == "user_stated"


@pytest.mark.asyncio
async def test_forget_uses_the_same_version_guard():
    gateway = FakeMemoryGateway()
    tools = {tool.name: tool.handler for tool in overlay(gateway).tools}
    stale = await tools["assistant_memory_forget"](
        {"entry_id": "mem_1", "expected_version": 1}
    )
    assert "重新搜索" in text_of(stale)
    ok = await tools["assistant_memory_forget"]({"entry_id": "mem_1", "expected_version": 3})
    assert "已删除" in text_of(ok)
    assert gateway.deleted == ["mem_1"]


@pytest.mark.asyncio
async def test_search_without_hits_is_explicit():
    gateway = FakeMemoryGateway()
    tools = {tool.name: tool.handler for tool in overlay(gateway).tools}
    result = await tools["assistant_memory_search"]({"query": "不存在的主题"})
    assert "没有匹配" in text_of(result)
