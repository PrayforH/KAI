"""VM provider construction, declaration parsing and container verification."""

import pytest
from pydantic import SecretStr

from harness.computer.control_server import ControllerSettings, create_app
from harness.computer.runtime import ComputerRuntime
from harness.computer.vm import PROVIDER as VM_PROVIDER
from harness.computer.vm import VmComputerDriver
from harness.computer.vm_host import VmComputerSpec, verify_vm_instance
from harness.config import Settings
from harness.storage.database import create_database

BASE = "sha256:" + "b" * 64


def configuration(**changes: object) -> Settings:
    return Settings.model_validate(
        {
            "assistant_computer_owners": {"tenant": ["user"]},
            "assistant_computer_control_url": "http://internal-node:8840",
            "assistant_computer_control_secret": SecretStr("private-fixture-" * 4),
            "assistant_computer_image": "sha256:" + "a" * 64,
            "sandbox_execution_mode": "worker_cli_deferred",
            **changes,
        }
    )


def spec(**changes: object) -> VmComputerSpec:
    return VmComputerSpec.model_validate(
        {
            "volume_ref": "kai-computer-fixture",
            "container_name": "kai-computer-fixture-vm",
            "ssh_host": "kai-computer-fixture-vm",
            "runner_image": "kai/computer-vm-runner:fixture",
            "guest_base_digest": BASE,
            "root_disk_source": "/data/disks/fixture/root.qcow2",
            "identity_dir": "/identities/computer_" + "c" * 32 + "-" + "d" * 32,
            "kvm_device": "/dev-host/kvm",
            "network": "kai-computer-net",
            **changes,
        }
    )


@pytest.mark.asyncio
async def test_vm_routing_is_disabled_without_an_image():
    engine, sessions = create_database("postgresql+asyncpg://harness:harness@127.0.0.1:1/unused")
    try:
        runtime = ComputerRuntime(configuration(), sessions, execution_enabled=True)
        try:
            assert VM_PROVIDER not in runtime.drivers
            assert runtime.default_provider == "docker-computer"
        finally:
            await runtime.close()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_vm_image_enables_provider_and_bad_digest_is_refused():
    engine, sessions = create_database("postgresql+asyncpg://harness:harness@127.0.0.1:1/unused")
    try:
        runtime = ComputerRuntime(
            configuration(assistant_computer_vm_image=BASE), sessions, execution_enabled=True
        )
        try:
            assert isinstance(runtime.drivers[VM_PROVIDER], VmComputerDriver)
        finally:
            await runtime.close()
        with pytest.raises(ValueError, match="personal Computer requires"):
            ComputerRuntime(
                configuration(assistant_computer_vm_image="guest:latest"),
                sessions,
                execution_enabled=True,
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_default_provider_must_be_configured():
    engine, sessions = create_database("postgresql+asyncpg://harness:harness@127.0.0.1:1/unused")
    try:
        with pytest.raises(ValueError, match="personal Computer requires"):
            ComputerRuntime(
                configuration(assistant_computer_default_provider="vm-computer"),
                sessions,
                execution_enabled=True,
            )
    finally:
        await engine.dispose()


def test_vm_spec_requires_immutable_digest_and_valid_identity_dir():
    with pytest.raises(ValueError):
        spec(guest_base_digest="guest:latest")
    malformed = spec(identity_dir="/identities/not-an-identity")
    with pytest.raises(RuntimeError, match="identity directory"):
        malformed.seed()


def test_vm_instance_verification_accepts_adopted_and_rejects_foreign():
    spec_ = spec(adopt=True)
    base = {
        "Id": "a" * 64,
        "Config": {"Image": spec_.runner_image, "Labels": {}},
        "Mounts": [
            {
                "Type": "bind",
                "Source": spec_.root_disk_source,
                "Destination": spec_.root_disk_target,
                "RW": True,
            },
            {
                "Type": "volume",
                "Name": spec_.volume_ref,
                "Destination": spec_.work_target,
                "RW": True,
            },
        ],
        "HostConfig": {
            "Devices": [{"PathOnHost": spec_.kvm_device, "PathInContainer": "/dev/kvm"}]
        },
    }
    assert verify_vm_instance(base, spec_) == "a" * 64

    labelled = {
        **base,
        "Config": {"Image": spec_.runner_image, "Labels": {"kai.computer.id": "other"}},
    }
    with pytest.raises(RuntimeError, match="belongs to another"):
        verify_vm_instance(labelled, spec_)

    unlabelled = {
        **base,
        "Config": {"Image": spec_.runner_image, "Labels": {}},
        "HostConfig": {},
    }
    with pytest.raises(RuntimeError, match="KVM"):
        verify_vm_instance(unlabelled, spec_)

    wrong_disk = {
        **base,
        "Mounts": [mount for mount in base["Mounts"] if mount["Type"] != "bind"],
    }
    with pytest.raises(RuntimeError, match="disks"):
        verify_vm_instance(wrong_disk, spec_)


def test_control_server_builds_vm_routes_from_declaration():
    settings = ControllerSettings(
        database_url="postgresql+asyncpg://harness:harness@127.0.0.1:1/unused",
        secret=SecretStr("private-fixture-" * 4),
        image="sha256:" + "a" * 64,
        vm_computers=[spec()],
    )
    app = create_app(settings)
    assert app is not None
