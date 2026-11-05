"""Canonical VM computer adoption and Worker routing against PostgreSQL.

These tests use the isolated integration database only; no production computer,
work volume or identity directory is touched.
"""

import pytest

from harness.computer.registry import ComputerRegistry
from harness.computer.runtime import ComputerRuntime
from harness.computer.vm import PROVIDER as VM_PROVIDER
from harness.computer.vm import VmComputerDriver
from harness.config import Settings
from harness.core.errors import ConflictError
from tests.integration.storage.conftest import DatabaseFixture

BASE = "sha256:" + "b" * 64


def runtime(database: DatabaseFixture) -> ComputerRuntime:
    _, sessions = database
    return ComputerRuntime(
        Settings.model_validate(
            {
                "assistant_computer_owners": {"tenant": ["user"]},
                "assistant_computer_control_url": "http://internal-node:8840",
                "assistant_computer_control_secret": "private-fixture-" * 4,
                "assistant_computer_image": "sha256:" + "a" * 64,
                "assistant_computer_vm_image": BASE,
                "sandbox_execution_mode": "worker_cli_deferred",
            }
        ),
        sessions,
        execution_enabled=True,
    )


@pytest.mark.asyncio
async def test_provider_routes_by_registry_row_and_fails_closed_for_unknown(
    database: DatabaseFixture,
):
    service = runtime(database)
    engine, _ = database
    try:
        session = type("Session", (), {"tenant_id": "tenant", "user_id": "user"})()
        # No row yet: the default provider answers.
        assert await service.provider_id(session) == "docker-computer"
        await service.registry.adopt(
            "tenant",
            "user",
            provider=VM_PROVIDER,
            image=BASE,
            computer_id="computer_" + "c" * 32,
            volume_ref="kai-computer-fixture",
        )
        assert await service.provider_id(session) == VM_PROVIDER
        driver = await service.resolve_driver("tenant", "user")
        assert isinstance(driver, VmComputerDriver)
        await service.registry.adopt(
            "tenant",
            "other",
            provider="foreign-provider",
            image=BASE,
            computer_id="computer_" + "e" * 32,
            volume_ref="kai-computer-fixture-2",
        )
        with pytest.raises(RuntimeError, match="not enabled"):
            await service.resolve_driver("tenant", "other")
    finally:
        await service.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_adopt_is_idempotent_and_refuses_a_different_identity(database: DatabaseFixture):
    service = runtime(database)
    engine, _ = database
    try:
        registry: ComputerRegistry = service.registry
        first = await registry.adopt(
            "tenant",
            "user",
            provider=VM_PROVIDER,
            image=BASE,
            computer_id="computer_" + "c" * 32,
            volume_ref="kai-computer-fixture",
        )
        assert first.provider == VM_PROVIDER and first.state.value == "registered"
        assert first.volume_ready is True
        again = await registry.adopt(
            "tenant",
            "user",
            provider=VM_PROVIDER,
            image=BASE,
            computer_id="computer_" + "c" * 32,
            volume_ref="kai-computer-fixture",
        )
        assert again.computer_id == first.computer_id and again.revision == first.revision
        with pytest.raises(ConflictError):
            await registry.adopt(
                "tenant",
                "user",
                provider=VM_PROVIDER,
                image=BASE,
                computer_id="computer_" + "f" * 32,
                volume_ref="kai-computer-fixture",
            )
    finally:
        await service.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_adopted_computer_reaches_ready_through_the_vm_driver(database: DatabaseFixture):
    """The registry flow is provider-neutral; adopt feeds the same start path."""

    service = runtime(database)
    engine, _ = database
    try:
        registry: ComputerRegistry = service.registry
        await registry.adopt(
            "tenant",
            "user",
            provider=VM_PROVIDER,
            image=BASE,
            computer_id="computer_" + "c" * 32,
            volume_ref="kai-computer-fixture",
        )
        token = await registry.claim_start("tenant", "user", owner="worker-fixture")
        computer = await registry.complete_start(
            token, provider_ref="a" * 64  # the node's adopted runner container id
        )
        assert computer.provider == VM_PROVIDER and computer.provider_ref == "a" * 64
        execution = await registry.claim_execution(
            "tenant", "user", owner="worker-fixture", run_id="run-a", session_id="session-a"
        )
        assert execution.computer_id == computer.computer_id
    finally:
        await service.close()
        await engine.dispose()
