from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from harness.adapters.memory import InMemoryAguiThreadBindingRepository
from harness.api.app import create_memory_app
from harness.core.models import AguiThreadBinding


@pytest.mark.asyncio
async def test_read_watermark_is_monotonic_and_does_not_reorder_tasks():
    repo = InMemoryAguiThreadBindingRepository()
    now = datetime.now(UTC)
    binding = AguiThreadBinding(
        tenant_id="t", user_id="u", thread_id="task", session_id="s", created_at=now, updated_at=now
    )
    await repo.add(binding)
    await repo.mark_read("t", "u", "task", read_at=now)
    updated = await repo.mark_read("t", "u", "task", read_at=now - timedelta(days=1))
    assert updated.last_read_at == now
    assert updated.updated_at == now
    assert (await repo.get_by_session("t", "u", "s")).last_read_at == now


@pytest.mark.asyncio
async def test_read_state_shared_between_devices_isolated_by_user_and_new_results_unread():
    app = create_memory_app(auto_execute=True)
    headers = {"X-Tenant-ID": "tenant-a", "X-User-ID": "user-1"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as first:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as second:
            await first.post(
                "/v1/agents",
                headers=headers,
                json={"path": "tests/fixtures/agents/echo-agent/agent.yaml"},
            )

            async def run(run_id):
                response = await first.post(
                    "/v1/agui?agent_name=echo-agent&agent_version=0.1.0",
                    headers=headers,
                    json={
                        "threadId": "shared",
                        "runId": run_id,
                        "state": {},
                        "messages": [{"id": run_id, "role": "user", "content": "hello"}],
                        "tools": [],
                        "context": [],
                        "forwardedProps": {},
                    },
                )
                assert response.status_code == 200

            await run("one")
            before = (await first.get("/v1/agui/threads", headers=headers)).json()[0]
            assert before["last_read_at"] is None
            response = await first.put(
                "/v1/agui/threads/shared/read",
                headers=headers,
                json={"updated_at": before["updated_at"]},
            )
            assert response.status_code == 200
            after = (await second.get("/v1/agui/threads", headers=headers)).json()[0]
            assert after["last_read_at"] == before["updated_at"]
            assert after["updated_at"] == before["updated_at"]
            for other in (
                {**headers, "X-User-ID": "user-2"},
                {**headers, "X-Tenant-ID": "tenant-b"},
            ):
                denied = await second.put(
                    "/v1/agui/threads/shared/read",
                    headers=other,
                    json={"updated_at": before["updated_at"]},
                )
                assert denied.status_code == 404
            # Future client clocks cannot suppress later results.
            future = await first.put(
                "/v1/agui/threads/shared/read",
                headers=headers,
                json={"updated_at": "2099-01-01T00:00:00Z"},
            )
            assert future.json()["last_read_at"] == before["updated_at"]
            await run("two")
            newer = (await second.get("/v1/agui/threads", headers=headers)).json()[0]
            assert newer["updated_at"] > newer["last_read_at"]
            # An older device's delayed acknowledgement keeps the new result unread.
            await first.put(
                "/v1/agui/threads/shared/read",
                headers=headers,
                json={"updated_at": before["updated_at"]},
            )
            newer = (await second.get("/v1/agui/threads", headers=headers)).json()[0]
            assert newer["last_read_at"] == before["updated_at"]
            invalid = await first.put(
                "/v1/agui/threads/shared/read",
                headers=headers,
                json={"updated_at": "2026-09-08T00:00:00"},
            )
            assert invalid.status_code == 422
