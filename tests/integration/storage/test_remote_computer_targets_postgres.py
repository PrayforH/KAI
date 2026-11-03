"""Switching a computer is an owner-authorized transaction, not data migration."""

import pytest

from harness.computer.desktop import DesktopControlService
from harness.computer.models import ComputerBusyError
from harness.computer.targets import ComputerTarget, ComputerTargetService
from harness.core.errors import NotFoundError
from tests.integration.storage.test_computer_switch_provider_postgres import (
    CID,
    NEW_IMAGE,
    OLD_IMAGE,
    seeded,
)


def service(registry):
    def target(name, provider, image, volume):
        return ComputerTarget(
            target_id=name,
            label=name,
            tenant_id="tenant-a",
            user_id="user-a",
            computer_id=CID,
            provider=provider,
            image=image,
            volume_ref=volume,
        )

    return ComputerTargetService(
        registry,
        [
            target("cloud", "vm-computer", OLD_IMAGE, "kai-computer-old"),
            target("local", "remote-computer", NEW_IMAGE, "remote-fixture"),
        ],
    )


@pytest.mark.asyncio
async def test_switch_and_return_preserve_source_and_fence_desktop_generation(database):
    registry = await seeded(database)
    selection = service(registry)
    desktop = DesktopControlService(database[1])
    before = await desktop.view("tenant-a", "user-a")
    await selection.select("tenant-a", "user-a", "local")
    assert (await selection.view("tenant-a", "user-a"))["active_target"] == "local"
    after = await desktop.view("tenant-a", "user-a")
    assert after.epoch > before.epoch
    await selection.select("tenant-a", "user-a", "cloud")
    saved = await registry.get("tenant-a", "user-a")
    assert saved.volume_ref == "kai-computer-old" and saved.computer_id == CID


@pytest.mark.asyncio
async def test_selection_cannot_redirect_another_owner_or_inject_a_host(database):
    selection = service(await seeded(database))
    for owner, target in [("other", "local"), ("user-a", "http://attacker")]:
        with pytest.raises(NotFoundError):
            await selection.select("tenant-a", owner, target)


@pytest.mark.asyncio
async def test_switch_refuses_live_execution_and_human_takeover(database):
    registry = await seeded(database)
    selection = service(registry)
    desktop = DesktopControlService(database[1])
    pending = await desktop.request_human("tenant-a", "user-a")
    await desktop.confirm_human("tenant-a", "user-a", expected_epoch=pending.epoch)
    with pytest.raises(ComputerBusyError):
        await selection.select("tenant-a", "user-a", "local")
    await desktop.release_human("tenant-a", "user-a")
    await registry.claim_execution(
        "tenant-a", "user-a", owner="worker", run_id="run", session_id="session"
    )
    with pytest.raises(ComputerBusyError):
        await selection.select("tenant-a", "user-a", "local")
    assert (await registry.get("tenant-a", "user-a")).provider == "docker-computer"
