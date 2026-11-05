"""Move one owner computer onto a different provider, with no data movement.

Maintenance entry for a pilot migration: the registration row keeps its stable
identity while the provider, base image and work volume change. The operator
must have verified the target instance and volume out of band. A live Run or a
non-idle state is refused, so this never races a running execution. Idempotent
when the target is already recorded and attached.
"""

from typing import Any

import pytest

from harness.computer.models import ComputerBusyError, ComputerState, LeasePurpose
from harness.computer.registry import ComputerRegistry
from harness.core.errors import ConflictError
from tests.integration.storage.conftest import DatabaseFixture

OLD_IMAGE = "sha256:" + "9" * 64
NEW_IMAGE = "sha256:" + "b" * 64
CID = "computer_" + "c" * 32


async def seeded(database: DatabaseFixture) -> ComputerRegistry:
    _, sessions = database
    registry = ComputerRegistry(sessions)
    await registry.adopt(
        "tenant-a",
        "user-a",
        provider="docker-computer",
        image=OLD_IMAGE,
        computer_id=CID,
        volume_ref="kai-computer-old",
    )
    token = await registry.claim_start("tenant-a", "user-a", owner="worker-a")
    await registry.complete_start(token, provider_ref="f" * 64)
    return registry


async def switch(registry: ComputerRegistry) -> Any:
    return await registry.switch_provider(
        "tenant-a",
        "user-a",
        computer_id=CID,
        provider="vm-computer",
        image=NEW_IMAGE,
        volume_ref="kai-computer-vm-work",
    )


@pytest.mark.asyncio
async def test_switch_records_the_new_provider_and_detaches_the_instance(
    database: DatabaseFixture,
) -> None:
    registry = await seeded(database)
    switched = await switch(registry)
    assert switched.provider == "vm-computer"
    assert switched.volume_ref == "kai-computer-vm-work"
    assert switched.image == NEW_IMAGE
    # The old instance must be re-attached by the new provider's own start.
    assert switched.provider_ref is None
    assert switched.state is ComputerState.REGISTERED
    assert switched.computer_id == CID
    # The previous image stays in history for a delayed old instance.
    assert OLD_IMAGE in switched.retired_images
    assert switched.volume_ready is True


@pytest.mark.asyncio
async def test_switch_refuses_a_live_holder(database: DatabaseFixture) -> None:
    registry = await seeded(database)
    await registry.claim_execution(
        "tenant-a", "user-a", owner="worker-a", run_id="run-a", session_id="session-a"
    )
    with pytest.raises(ComputerBusyError):
        await switch(registry)


@pytest.mark.asyncio
async def test_switch_refuses_a_mismatched_computer_id(database: DatabaseFixture) -> None:
    registry = await seeded(database)
    with pytest.raises(ConflictError):
        await registry.switch_provider(
            "tenant-a",
            "user-a",
            computer_id="computer_" + "d" * 32,
            provider="vm-computer",
            image=NEW_IMAGE,
            volume_ref="kai-computer-vm-work",
        )


@pytest.mark.asyncio
async def test_switch_is_idempotent_after_the_new_provider_attached(
    database: DatabaseFixture,
) -> None:
    registry = await seeded(database)
    await switch(registry)
    token = await registry.claim_start("tenant-a", "user-a", owner="worker-a")
    attached = await registry.complete_start(token, provider_ref="a" * 64)
    assert attached.provider == "vm-computer"
    again = await switch(registry)
    assert again.provider_ref == "a" * 64 and again.revision == attached.revision
    # The new provider's first execution works exactly like any other.
    lease = await registry.claim_execution(
        "tenant-a", "user-a", owner="worker-a", run_id="run-b", session_id="session-a"
    )
    assert lease.purpose is LeasePurpose.EXECUTE
