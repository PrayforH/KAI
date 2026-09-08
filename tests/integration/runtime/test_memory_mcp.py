from datetime import timedelta

import httpx
import pytest
from httpx import ASGITransport
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from harness.api.app import create_memory_app
from harness.core.models import ExecutionIdentity


def identity() -> ExecutionIdentity:
    return ExecutionIdentity(
        tenant_id="tenant-a",
        user_id="user-a",
        project_id="agent-a",
        session_id="session-a",
        run_id="run-a",
        agent_name="agent-a",
        agent_version="1.0.0",
    )


@pytest.mark.asyncio
async def test_remote_memory_mcp_uses_workload_identity_and_creates_proposal() -> None:
    app = create_memory_app()
    container = app.state.container
    token = container.memory_workload_tokens.issue(identity())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://memory",
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            async with streamable_http_client(
                "http://memory/mcp/memory/mcp", http_client=client
            ) as (read_stream, write_stream, _session_id):
                async with ClientSession(
                    read_stream,
                    write_stream,
                    read_timeout_seconds=timedelta(seconds=5),
                ) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    result = await session.call_tool(
                        "propose_memory", {"content": "用户偏好使用中文"}
                    )

    assert [tool.name for tool in tools.tools] == ["propose_memory", "search_memory", "read_memory"]
    assert result.isError is not True
    entries = await container.memory_bank.list_entries("tenant-a", "user-a")
    assert len(entries) == 1
    assert entries[0].source.run_id == "run-a"
    assert entries[0].status.value == "pending"


@pytest.mark.asyncio
async def test_remote_memory_mcp_rejects_missing_and_wrong_purpose_tokens() -> None:
    app = create_memory_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://memory"
        ) as client:
            missing = await client.post("/mcp/memory/mcp", json={})
            invalid = await client.post(
                "/mcp/memory/mcp",
                json={},
                headers={"Authorization": "Bearer invalid"},
            )

    assert missing.status_code == 401
    assert invalid.status_code == 401
