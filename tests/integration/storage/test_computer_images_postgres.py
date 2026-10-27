"""Image replacement keeps owner disks and fences uncertain or stale operations."""

import asyncio
import copy

import httpx
import pytest
from sqlalchemy import delete, update

from harness.computer.host import ComputerLimits, DockerComputerLifecycle, verify_instance
from harness.computer.manager import ComputerManager
from harness.computer.models import (
    AssistantComputer,
    ComputerBusyError,
    ComputerLease,
    ComputerLeaseLostError,
    ComputerStartRejectedError,
    ComputerState,
)
from harness.computer.registry import ComputerRegistry
from harness.core.errors import ConflictError
from harness.storage.models import TenantMembershipRow, UserRow
from tests.integration.storage.conftest import DatabaseFixture
from tests.integration.storage.test_computer_access_postgres import seed
from tests.integration.storage.test_computer_host_postgres import Engine
from tests.integration.storage.test_computer_manager_postgres import Driver
from tests.integration.storage.test_computer_registry_postgres import expire

TARGET = "sha256:" + "b" * 64


class UpgradeDriver(Driver):
    image = TARGET

    def __init__(self, registry: ComputerRegistry):
        super().__init__()
        self.registry = registry
        self.unavailable = self.uncertain_after_commit = False
        self.token: ComputerLease | None = None

    async def validate_image(self) -> None:
        self.calls.append("check-image")
        if self.unavailable:
            raise ComputerStartRejectedError("fixture image missing")

    async def upgrade(self, computer: AssistantComputer, token: ComputerLease) -> None:
        self.calls.append("upgrade")
        self.token = token
        await self.registry.record_image_change(
            token, expected_image=computer.image, image=self.image
        )
        if self.uncertain_after_commit:
            raise TimeoutError("fixture lost image-change reply")


@pytest.mark.asyncio
async def test_explicit_image_change_preserves_identity_disk_and_history(database: DatabaseFixture):
    _, sessions = database
    registry = ComputerRegistry(sessions)
    original = await ComputerManager(registry, Driver(), owner="old").ready("t", "u")
    driver = UpgradeDriver(registry)
    manager = ComputerManager(registry, driver, owner="upgrade")
    changed = await manager.migrate_image("t", "u", expected_image=original.image)
    assert changed.image == TARGET and changed.retired_images == (original.image,)
    assert changed.state is ComputerState.REGISTERED and changed.volume_ready
    assert (changed.computer_id, changed.volume_ref) == (original.computer_id, original.volume_ref)
    assert driver.calls == ["check-image", "upgrade"]
    changed = await manager.ready("t", "u")
    assert changed.state is ComputerState.IDLE
    # Repeating the requested target does not reset or create it again.
    assert (await manager.migrate_image("t", "u", expected_image=original.image)).image == TARGET
    assert driver.calls.count("upgrade") == driver.calls.count("start") == 1


@pytest.mark.parametrize("failure", ["unavailable", "busy", "source-changed"])
@pytest.mark.asyncio
async def test_rejected_upgrade_does_not_reset_or_change_old_computer(
    database: DatabaseFixture, failure: str
):
    _, sessions = database
    registry = ComputerRegistry(sessions)
    before = await ComputerManager(registry, Driver(), owner="old").ready("t", "u")
    driver = UpgradeDriver(registry)
    if failure == "unavailable":
        driver.unavailable = True
    elif failure == "busy":
        await registry.claim_execution("t", "u", owner="worker", run_id="r", session_id="s")
    before = await registry.get("t", "u")
    expected = before.image if failure != "source-changed" else "wrong-source"
    with pytest.raises((ComputerStartRejectedError, ComputerBusyError, ConflictError)):
        await ComputerManager(registry, driver, owner="upgrade").migrate_image(
            "t", "u", expected_image=expected
        )
    assert await registry.get("t", "u") == before
    assert driver.calls == ["check-image"]


@pytest.mark.asyncio
async def test_lost_upgrade_reply_retains_lease_until_fenced_recovery(database: DatabaseFixture):
    _, sessions = database
    registry = ComputerRegistry(sessions)
    before = await ComputerManager(registry, Driver(), owner="old").ready("t", "u")
    driver = UpgradeDriver(registry)
    driver.uncertain_after_commit = True
    manager = ComputerManager(registry, driver, owner="upgrade")
    with pytest.raises(TimeoutError):
        await manager.migrate_image("t", "u", expected_image=before.image)
    current = await registry.get("t", "u")
    assert current.image == TARGET and current.state is ComputerState.RECOVERING
    assert current.provider_ref is None and current.volume_ref == before.volume_ref
    with pytest.raises(ComputerBusyError):
        await manager.ready("t", "u")
    assert driver.token is not None
    await expire(sessions, driver.token)
    current = await manager.ready("t", "u")
    assert current.image == TARGET and current.state is ComputerState.IDLE
    assert current.volume_ref == before.volume_ref
    assert driver.calls[-2:] == ["recover", "start"]
    with pytest.raises(ComputerLeaseLostError):
        await registry.record_image_change(driver.token, expected_image=TARGET, image=TARGET)


@pytest.mark.asyncio
async def test_host_upgrade_fences_old_id_and_retains_both_disks(database: DatabaseFixture):
    _, sessions = database
    registry, engine = ComputerRegistry(sessions), Engine()
    original = await registry.ensure("t", "u", provider="docker-computer", image="computer:v0")
    async with httpx.AsyncClient(
        base_url="http://docker", transport=httpx.MockTransport(engine.request)
    ) as client:
        old_host = DockerComputerLifecycle(registry, client, ComputerLimits(original.image))
        start = await registry.claim_start("t", "u", owner="old")
        old_id = await old_host.lifecycle(start, "ensure")
        assert old_id is not None and engine.container is not None
        old_info = copy.deepcopy(engine.container)
        await registry.complete_start(start, provider_ref=old_id)
        new_host = DockerComputerLifecycle(registry, client, ComputerLimits(TARGET))
        await new_host.check_image(TARGET)
        token = await registry.claim_recovery(
            "t", "u", owner="upgrade", expected_image=original.image
        )
        assert await new_host.lifecycle(token, "upgrade", image=TARGET) is None
        switched = await registry.get("t", "u")
        assert switched.state is ComputerState.RECOVERING and switched.image == TARGET
        assert switched.retired_images == (original.image,) and switched.provider_ref is None
        assert ("DELETE", "/containers/" + old_id) in engine.calls and len(engine.volumes) == 2
        await registry.complete_recovery(token, provider_ref=None)
        start = await registry.claim_start("t", "u", owner="new")
        new_id = await new_host.lifecycle(start, "ensure")
        assert new_id != old_id and engine.container is not None
        assert engine.container["Config"]["Image"] == TARGET
        assert set(engine.volumes) == {original.volume_ref, original.volume_ref + "-control"}
        with pytest.raises(RuntimeError, match="identity"):
            verify_instance(old_info, switched)
        assert verify_instance(old_info, switched, allow_retired=True) == old_id


@pytest.mark.parametrize("invalid", ["unknown-image", "wrong-owner", "wrong-disk"])
@pytest.mark.asyncio
async def test_retired_image_never_widens_recovery_to_unowned_resources(
    database: DatabaseFixture, invalid: str
):
    _, sessions = database
    registry, engine = ComputerRegistry(sessions), Engine()
    original = await registry.ensure("t", "u", provider="docker-computer", image="computer:v0")
    async with httpx.AsyncClient(
        base_url="http://docker", transport=httpx.MockTransport(engine.request)
    ) as client:
        host = DockerComputerLifecycle(registry, client, ComputerLimits(original.image))
        start = await registry.claim_start("t", "u", owner="old")
        reference = await host.lifecycle(start, "ensure")
        assert reference is not None and engine.container is not None
        await registry.complete_start(start, provider_ref=reference)
        token = await registry.claim_recovery("t", "u", owner="new")
        # Simulate a confirmed switch with a delayed old owned create appearing later.
        await registry.record_image_change(token, expected_image=original.image, image=TARGET)
        if invalid == "unknown-image":
            engine.container["Config"]["Image"] = "unrecorded-image"
        elif invalid == "wrong-owner":
            engine.container["Config"]["Labels"]["kai.computer.user"] = "other"
        else:
            engine.container["Mounts"][0]["Name"] = "other-disk"
        with pytest.raises(RuntimeError):
            await host.lifecycle(token, "reset")
        assert not any(method == "DELETE" for method, _ in engine.calls)


@pytest.mark.asyncio
async def test_delayed_retired_instance_can_be_recovered_but_not_executed(
    database: DatabaseFixture,
):
    _, sessions = database
    registry, engine = ComputerRegistry(sessions), Engine()
    original = await registry.ensure("t", "u", provider="docker-computer", image="computer:v0")
    async with httpx.AsyncClient(
        base_url="http://docker", transport=httpx.MockTransport(engine.request)
    ) as client:
        old_host = DockerComputerLifecycle(registry, client, ComputerLimits(original.image))
        start = await registry.claim_start("t", "u", owner="old")
        reference = await old_host.lifecycle(start, "ensure")
        assert reference is not None and engine.container is not None
        await registry.complete_start(start, provider_ref=reference)
        stale = copy.deepcopy(engine.container)
        token = await registry.claim_recovery("t", "u", owner="upgrade")
        host = DockerComputerLifecycle(registry, client, ComputerLimits(TARGET))
        await host.lifecycle(token, "upgrade", image=TARGET)
        await registry.complete_recovery(token, provider_ref=None)
        # Earlier Docker create arrives after the upgrade acknowledged deletion.
        engine.container = stale
        start = await registry.claim_start("t", "u", owner="new")
        with pytest.raises(RuntimeError, match="identity"):
            await host.lifecycle(start, "ensure")
        await expire(sessions, start)
        recovery = await registry.claim_recovery("t", "u", owner="recover")
        await host.lifecycle(recovery, "reset")
        await registry.complete_recovery(recovery, provider_ref=None)
        next_start = await registry.claim_start("t", "u", owner="retry")
        assert await host.lifecycle(next_start, "ensure") != reference
        assert len(engine.volumes) == 2


@pytest.mark.parametrize("failure", ["wrong-target", "mutable-target", "stale-token"])
@pytest.mark.asyncio
async def test_upgrade_preflight_and_host_queue_reject_invalid_targets(
    database: DatabaseFixture, failure: str
):
    _, sessions = database
    registry, engine = ComputerRegistry(sessions), Engine()
    original = await registry.ensure("t", "u", provider="docker-computer", image="computer:v0")
    token = await registry.claim_recovery("t", "u", owner="operator")
    async with httpx.AsyncClient(
        base_url="http://docker", transport=httpx.MockTransport(engine.request)
    ) as client:
        host = DockerComputerLifecycle(registry, client, ComputerLimits(TARGET))
        if failure == "stale-token":
            async with registry.host_operation(token):
                queued = asyncio.create_task(host.lifecycle(token, "upgrade", image=TARGET))
                await asyncio.sleep(0.03)
                await expire(sessions, token)
                await registry.claim_recovery("t", "u", owner="new")
            with pytest.raises(ComputerLeaseLostError):
                await queued
        else:
            target = "sha256:" + "c" * 64 if failure == "wrong-target" else "mutable:latest"
            with pytest.raises(ComputerStartRejectedError):
                await host.lifecycle(token, "upgrade", image=target)
        assert engine.calls == []
        assert (await registry.get("t", "u")).image == original.image


@pytest.mark.parametrize("change", ["none", "disabled", "membership", "not-enabled"])
@pytest.mark.asyncio
async def test_maintenance_upgrade_uses_active_owner_without_borrowing_a_run(
    database: DatabaseFixture, change: str
):
    access, registry, computer, execution, _, _, session, _ = await seed(database)
    with pytest.raises(ComputerLeaseLostError):
        await access.authorize(computer, execution, "upgrade")
    await registry.finish_execution(execution)
    token = await registry.claim_recovery(session.tenant_id, session.user_id, owner="maintenance")
    _, sessions = database
    if change == "not-enabled":
        access.owners = {}
    else:
        async with sessions.begin() as db:
            if change == "disabled":
                await db.execute(update(UserRow).values(disabled=True))
            elif change == "membership":
                await db.execute(delete(TenantMembershipRow))
    if change == "none":
        await access.authorize(computer, token, "upgrade")
    else:
        with pytest.raises(ComputerLeaseLostError):
            await access.authorize(computer, token, "upgrade")
        await access.authorize(computer, token, "reset")
