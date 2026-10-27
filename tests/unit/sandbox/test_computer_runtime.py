import pytest
from pydantic import SecretStr

from harness.computer.control_server import ControllerSettings, create_app
from harness.computer.runtime import ComputerRuntime
from harness.config import Settings
from harness.storage.database import create_database


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


@pytest.mark.parametrize(
    "changes",
    [
        {"assistant_computer_control_url": "http://user:password@node"},
        {"assistant_computer_control_url": "http://node/path"},
        {"assistant_computer_control_url": "http://node?secret=fixture"},
        {"assistant_computer_control_url": "file:///var/run/docker.sock"},
        {"assistant_computer_control_secret": SecretStr("short")},
        {"assistant_computer_image": "computer:latest"},
        {"sandbox_execution_mode": "remote_cli"},
    ],
)
@pytest.mark.asyncio
async def test_enabled_worker_requires_private_node_pinned_image_and_compatible_mode(
    changes: dict[str, object],
):
    engine, sessions = create_database("postgresql+asyncpg://harness:harness@127.0.0.1:1/unused")
    try:
        with pytest.raises(ValueError, match="personal Computer requires"):
            ComputerRuntime(configuration(**changes), sessions, execution_enabled=True)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_disabled_worker_and_api_only_routing_need_no_node_secret():
    engine, sessions = create_database("postgresql+asyncpg://harness:harness@127.0.0.1:1/unused")
    try:
        disabled = ComputerRuntime(
            configuration(
                assistant_computer_owners={}, assistant_computer_control_secret=SecretStr("")
            ),
            sessions,
            execution_enabled=True,
        )
        assert not disabled.enabled and disabled.driver is None
        api = ComputerRuntime(
            configuration(assistant_computer_control_secret=SecretStr("")),
            sessions,
            execution_enabled=False,
        )
        assert api.enabled and api.driver is None
        await disabled.close()
        await api.close()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_enabled_worker_constructs_only_private_http_transport_without_connecting():
    engine, sessions = create_database("postgresql+asyncpg://harness:harness@127.0.0.1:1/unused")
    runtime = ComputerRuntime(configuration(), sessions, execution_enabled=True)
    try:
        assert runtime.enabled and runtime.driver is not None
        assert runtime.driver.image == "sha256:" + "a" * 64
    finally:
        await runtime.close()
        await engine.dispose()


def test_unchecked_probe_authorization_cannot_be_selected_for_a_production_database():
    with pytest.raises(ValueError, match="isolated Computer probe"):
        create_app(
            ControllerSettings(
                database_url="postgresql+asyncpg://harness:harness@127.0.0.1:1/production",
                secret=SecretStr("private-fixture-" * 4),
                image="sha256:" + "a" * 64,
                authorization="probe",
            )
        )
