"""Host lifecycle serializes physical effects without holding ownership row locks."""

import asyncio
import copy
import json
from typing import Any

import httpx
import pytest

from harness.computer.host import ComputerLimits, DockerComputerLifecycle
from harness.computer.models import ComputerLeaseLostError
from harness.computer.registry import ComputerRegistry
from tests.integration.storage.conftest import DatabaseFixture
from tests.integration.storage.test_computer_registry_postgres import expire


class Engine:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.volumes: dict[str, dict[str, Any]] = {}
        self.container: dict[str, Any] | None = None
        self.lose_create_reply = False
        self.sequence = 0
        self.create_started = asyncio.Event()
        self.allow_create: asyncio.Event | None = None

    async def request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.calls.append((request.method, path))
        if path.startswith("/images/"):
            return httpx.Response(
                200, json={"Config": {"Labels": {"org.shdata.kai.computer.guard": "1"}}}
            )
        if path == "/volumes/create":
            value = json.loads(request.content)
            self.volumes[value["Name"]] = value
            return httpx.Response(201, json=value)
        if path.startswith("/volumes/"):
            value = self.volumes.get(path.removeprefix("/volumes/"))
            return httpx.Response(200, json=value) if value else httpx.Response(404)
        if path == "/containers/create":
            self.create_started.set()
            if self.allow_create:
                await self.allow_create.wait()
            if self.container:
                return httpx.Response(409)
            body = json.loads(request.content)
            self.sequence += 1
            self.container = {
                "Id": f"{self.sequence:064x}",
                "Name": request.url.params["name"],
                "Config": copy.deepcopy(body),
                "HostConfig": body["HostConfig"],
                "State": {"Running": False},
                "Mounts": [
                    {"Type": "volume", "Name": m["Source"], "Destination": m["Target"], "RW": True}
                    for m in body["HostConfig"]["Mounts"]
                ],
            }
            if self.lose_create_reply:
                self.lose_create_reply = False
                raise httpx.ReadTimeout("fixture acknowledgement lost", request=request)
            return httpx.Response(201, json={"Id": self.container["Id"]})
        if path.startswith("/containers/"):
            if not self.container:
                return httpx.Response(404)
            if request.method == "DELETE":
                assert path == "/containers/" + self.container["Id"]
                self.container = None
                return httpx.Response(204)
            if path.endswith("/start"):
                assert path == "/containers/" + self.container["Id"] + "/start"
                self.container["State"]["Running"] = True
                return httpx.Response(204)
            return httpx.Response(200, json=self.container)
        raise AssertionError("unexpected Docker request")


@pytest.mark.asyncio
async def test_lost_create_reply_reuses_one_stable_name(database: DatabaseFixture) -> None:
    _, sessions = database
    registry, engine = ComputerRegistry(sessions), Engine()
    await registry.ensure("t", "u", provider="docker-computer", image="computer:v0")
    token = await registry.claim_start("t", "u", owner="manager")
    async with httpx.AsyncClient(
        base_url="http://docker", transport=httpx.MockTransport(engine.request)
    ) as client:
        host = DockerComputerLifecycle(registry, client, ComputerLimits("computer:v0"))
        engine.lose_create_reply = True
        with pytest.raises(httpx.ReadTimeout):
            await host.lifecycle(token, "ensure")
        first = engine.container
        assert first is not None
        result = await host.lifecycle(token, "ensure")
        assert result == first["Id"]
        assert engine.calls.count(("POST", "/containers/create")) == 1


@pytest.mark.asyncio
async def test_host_gate_does_not_block_database_heartbeat(database: DatabaseFixture) -> None:
    _, sessions = database
    registry, engine = ComputerRegistry(sessions), Engine()
    await registry.ensure("t", "u", provider="docker-computer", image="computer:v0")
    token = await registry.claim_start("t", "u", owner="manager")
    engine.allow_create = asyncio.Event()
    async with httpx.AsyncClient(
        base_url="http://docker", transport=httpx.MockTransport(engine.request)
    ) as client:
        host = DockerComputerLifecycle(registry, client, ComputerLimits("computer:v0"))
        task = asyncio.create_task(host.lifecycle(token, "ensure"))
        try:
            async with asyncio.timeout(3):
                await engine.create_started.wait()
                renewed = await registry.renew(token)
            assert renewed.expires_at > token.expires_at
        finally:
            engine.allow_create.set()
            await task


@pytest.mark.asyncio
async def test_queued_stale_host_request_revalidates_after_the_lock(
    database: DatabaseFixture,
) -> None:
    _, sessions = database
    registry, engine = ComputerRegistry(sessions), Engine()
    await registry.ensure("t", "u", provider="docker-computer", image="computer:v0")
    token = await registry.claim_start("t", "u", owner="manager")
    async with httpx.AsyncClient(
        base_url="http://docker", transport=httpx.MockTransport(engine.request)
    ) as client:
        host = DockerComputerLifecycle(registry, client, ComputerLimits("computer:v0"))
        async with registry.host_operation(token):
            queued = asyncio.create_task(host.lifecycle(token, "ensure"))
            await asyncio.sleep(0.05)
            await expire(sessions, token)
            await registry.claim_recovery("t", "u", owner="new-manager")
        with pytest.raises(ComputerLeaseLostError):
            await queued
    assert engine.calls == []


@pytest.mark.asyncio
async def test_missing_saved_work_disk_never_creates_an_empty_replacement(
    database: DatabaseFixture,
) -> None:
    _, sessions = database
    registry, engine = ComputerRegistry(sessions), Engine()
    await registry.ensure("t", "u", provider="docker-computer", image="computer:v0")
    token = await registry.claim_start("t", "u", owner="manager")
    await registry.complete_start(token, provider_ref="a" * 64)
    recovery = await registry.claim_recovery("t", "u", owner="manager")
    await registry.complete_recovery(recovery, provider_ref=None)
    token = await registry.claim_start("t", "u", owner="manager")
    async with httpx.AsyncClient(
        base_url="http://docker", transport=httpx.MockTransport(engine.request)
    ) as client:
        with pytest.raises(RuntimeError, match="missing"):
            await DockerComputerLifecycle(
                registry, client, ComputerLimits("computer:v0")
            ).lifecycle(token, "ensure")
    assert ("POST", "/volumes/create") not in engine.calls


@pytest.mark.asyncio
async def test_recovery_fences_by_id_and_preserves_both_owned_disks(
    database: DatabaseFixture,
) -> None:
    _, sessions = database
    registry, engine = ComputerRegistry(sessions), Engine()
    await registry.ensure("t", "u", provider="docker-computer", image="computer:v0")
    token = await registry.claim_start("t", "u", owner="manager")
    async with httpx.AsyncClient(
        base_url="http://docker", transport=httpx.MockTransport(engine.request)
    ) as client:
        host = DockerComputerLifecycle(registry, client, ComputerLimits("computer:v0"))
        reference = await host.lifecycle(token, "ensure")
        assert reference is not None
        await registry.complete_start(token, provider_ref=reference)
        recovery = await registry.claim_recovery("t", "u", owner="manager")
        assert await host.lifecycle(recovery, "reset") is None
        assert ("DELETE", "/containers/" + reference) in engine.calls
        assert len(engine.volumes) == 2


@pytest.mark.asyncio
async def test_mismatched_container_owner_is_never_deleted(database: DatabaseFixture) -> None:
    _, sessions = database
    registry, engine = ComputerRegistry(sessions), Engine()
    await registry.ensure("t", "u", provider="docker-computer", image="computer:v0")
    token = await registry.claim_start("t", "u", owner="manager")
    async with httpx.AsyncClient(
        base_url="http://docker", transport=httpx.MockTransport(engine.request)
    ) as client:
        host = DockerComputerLifecycle(registry, client, ComputerLimits("computer:v0"))
        reference = await host.lifecycle(token, "ensure")
        assert reference is not None and engine.container is not None
        await registry.complete_start(token, provider_ref=reference)
        recovery = await registry.claim_recovery("t", "u", owner="manager")
        engine.container["Config"]["Labels"]["kai.computer.user"] = "other-user"
        with pytest.raises(RuntimeError, match="identity"):
            await host.lifecycle(recovery, "reset")
    assert not any(method == "DELETE" for method, _ in engine.calls)
