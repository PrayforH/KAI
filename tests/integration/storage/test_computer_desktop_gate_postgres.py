"""The desktop operation gate: execution lease, control lease and epoch.

Uses the isolated integration database and a stub screen service; no guest,
SSH key or Docker daemon is touched. The stub records which operations the
gate let through.
"""

from datetime import UTC, datetime
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from harness.assistant.constants import PERSONAL_ASSISTANT_THREAD
from harness.computer.control_server import ControllerSettings, create_app
from harness.computer.desktop import DesktopControlService, DesktopState
from harness.computer.models import ComputerLease
from harness.computer.registry import ComputerRegistry
from harness.core.models import AguiThreadBinding, Run, RunStatus, Session
from harness.sandbox.lease import SandboxLeaseService
from harness.storage.database import SessionFactory
from harness.storage.models import (
    AguiThreadBindingRow,
    SessionRow,
    TenantMembershipRow,
    UserRow,
)
from harness.storage.repositories import PostgresRunRepository
from harness.storage.sandbox_lease_repository import PostgresSandboxLeaseRepository
from tests.integration.storage.conftest import DatabaseFixture

SECRET = "private-fixture-" * 4
BASE = "sha256:" + "b" * 64
NOW = datetime(2026, 10, 28, 5, 0, tzinfo=UTC)
TENANT, USER, HANDLE = "fixture-tenant", "fixture-user", "computer-run-" + "a" * 32


async def seed(sessions: SessionFactory) -> ComputerLease:
    """The full canonical chain for one VM computer execution lease."""

    session = Session(
        tenant_id=TENANT,
        user_id=USER,
        session_id="fixture-session",
        agent_name="assistant",
        agent_version="1",
        created_at=NOW,
    )
    binding = AguiThreadBinding(
        tenant_id=TENANT,
        user_id=USER,
        thread_id=PERSONAL_ASSISTANT_THREAD,
        session_id=session.session_id,
        created_at=NOW,
        updated_at=NOW,
    )
    async with sessions.begin() as db:
        db.add(
            UserRow(
                user_id=USER,
                email="fixture@invalid.local",
                display_name="fixture",
                created_at=NOW,
                updated_at=NOW,
            )
        )
        db.add(TenantMembershipRow(tenant_id=TENANT, user_id=USER, role="owner", created_at=NOW))
        db.add(
            SessionRow(
                tenant_id=TENANT,
                session_id=session.session_id,
                user_id=USER,
                payload=session.model_dump(mode="json"),
            )
        )
        db.add(
            AguiThreadBindingRow(
                tenant_id=TENANT,
                user_id=USER,
                thread_id=binding.thread_id,
                session_id=binding.session_id,
                payload=binding.model_dump(mode="json"),
            )
        )
    run = Run(
        tenant_id=TENANT,
        session_id=session.session_id,
        run_id="fixture-run",
        status=RunStatus.RUNNING,
        fencing_token=2,
        idempotency_key="fixture-key",
        created_at=NOW,
        updated_at=NOW,
    )
    await PostgresRunRepository(sessions, fence_computers=True).add(run)
    sandbox_leases = SandboxLeaseService(PostgresSandboxLeaseRepository(sessions))
    await sandbox_leases.acquire(
        tenant_id=TENANT,
        session_id=session.session_id,
        run_id=run.run_id,
        owner="run-fence:1",
        provider="vm-computer",
        sandbox_id=HANDLE,
    )
    registry = ComputerRegistry(sessions)
    await registry.adopt(
        TENANT,
        USER,
        provider="vm-computer",
        image=BASE,
        computer_id="computer_" + "c" * 32,
        volume_ref="kai-computer-fixture",
    )
    start = await registry.claim_start(TENANT, USER, owner=HANDLE)
    await registry.complete_start(start, provider_ref="vm-fixture")
    return await registry.claim_execution(
        TENANT,
        USER,
        owner=HANDLE,
        run_id=run.run_id,
        session_id=session.session_id,
        run_fencing_token=run.fencing_token,
    )


class StubScreen:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def operate(
        self, computer: Any, operation: str, payload: dict[str, Any]
    ) -> dict[str, object]:
        self.calls.append((computer.computer_id, operation))
        return {"ok": True}

    async def close(self) -> None:
        return None


async def prepare(sessions: SessionFactory) -> ComputerLease:
    return await seed(sessions)


def desktop_request(token: ComputerLease, operation: str, epoch: int) -> dict[str, Any]:
    return {
        "token": token.model_dump(mode="json"),
        "operation": operation,
        "payload": {"x": 1, "y": 2},
        "expected_epoch": epoch,
    }


@pytest.mark.asyncio
async def test_gate_allows_the_current_holder_with_the_current_epoch(
    database: DatabaseFixture,
) -> None:
    _, sessions = database
    app = create_app(
        ControllerSettings(
            database_url="postgresql+asyncpg://harness:harness@127.0.0.1:35432/harness_test",
            secret=SecretStr(SECRET),
            image="sha256:" + "a" * 64,
            authorization="canonical",
            owners={TENANT: frozenset({USER})},
        )
    )
    lease = await prepare(sessions)
    desktop = DesktopControlService(sessions)
    state = await desktop.acquire_agent(lease.tenant_id, lease.user_id, owner=lease.owner)
    assert state.state is DesktopState.AGENT
    stub = StubScreen()
    cast(Any, app.state).desktop_screen = stub
    with cast(Any, TestClient(app, raise_server_exceptions=False)) as client:
        response = client.post(
            "/v1/computer/desktop",
            json=desktop_request(lease, "move", state.epoch),
            headers={"Authorization": "Bearer " + SECRET},
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"ok": True}
        assert stub.calls == [(lease.computer_id, "move")]


@pytest.mark.asyncio
async def test_gate_rejects_stale_epochs_released_desktops_and_wrong_secret(
    database: DatabaseFixture,
) -> None:
    _, sessions = database
    app = create_app(
        ControllerSettings(
            database_url="postgresql+asyncpg://harness:harness@127.0.0.1:35432/harness_test",
            secret=SecretStr(SECRET),
            image="sha256:" + "a" * 64,
            authorization="canonical",
            owners={TENANT: frozenset({USER})},
        )
    )
    lease = await prepare(sessions)
    desktop = DesktopControlService(sessions)
    state = await desktop.acquire_agent(lease.tenant_id, lease.user_id, owner=lease.owner)
    stub = StubScreen()
    cast(Any, app.state).desktop_screen = stub
    with cast(Any, TestClient(app, raise_server_exceptions=False)) as client:
        headers = {"Authorization": "Bearer " + SECRET}
        # An epoch from before the latest ownership change is fenced.
        response = client.post(
            "/v1/computer/desktop",
            json=desktop_request(lease, "move", state.epoch + 7),
            headers=headers,
        )
        assert response.status_code == 409
        # The gate never grants from observation: after release, the same
        # holder with its old epoch is refused and nothing reaches the screen.
        await desktop.release_agent(lease.tenant_id, lease.user_id, owner=lease.owner)
        response = client.post(
            "/v1/computer/desktop",
            json=desktop_request(lease, "move", state.epoch),
            headers=headers,
        )
        assert response.status_code == 409
        assert stub.calls == []
        # A wrong control secret never reaches the gate.
        response = client.post(
            "/v1/computer/desktop",
            json=desktop_request(lease, "move", state.epoch),
            headers={"Authorization": "Bearer wrong-secret"},
        )
        assert response.status_code == 403
