"""Physical lifecycle primitives needed by the persistent Computer driver."""

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from harness.sandbox.opensandbox import OpenSandboxClient, OpenSandboxWorkVolume


@pytest.mark.parametrize("path", ["/", "relative", "/a/../b", "/a//b", "/a/", "/a\n"])
def test_work_volume_rejects_uncontrolled_mount_roots(path: str) -> None:
    with pytest.raises(ValueError):
        OpenSandboxWorkVolume(name="work", claim_name="kai-owner", mount_path=path)


@pytest.mark.asyncio
async def test_work_disk_is_retained_when_instance_terminates() -> None:
    bodies: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            bodies.append(json.loads(request.content))
            return httpx.Response(202, json={"id": "instance-a"})
        if request.url.path.endswith("/ping"):
            return httpx.Response(200)
        return httpx.Response(200, json={"status": {"state": "Running"}})

    client = OpenSandboxClient(
        api_url="https://sandbox.example", api_key="key", transport=httpx.MockTransport(handler)
    )
    try:
        await client.create(
            image="desktop:v1",
            timeout=600,
            entrypoint=["tail", "-f", "/dev/null"],
            resource_limits={},
            metadata={"harness.computer": "owner-a"},
            work_volumes=[
                OpenSandboxWorkVolume(name="work", claim_name="kai-owner", mount_path="/home/kai")
            ],
            environment={"HOME": "/home/kai"},
        )
    finally:
        await client.aclose()
    assert bodies[0]["volumes"] == [
        {
            "name": "work",
            "mountPath": "/home/kai",
            "readOnly": False,
            "pvc": {
                "claimName": "kai-owner",
                "createIfNotExists": True,
                "deleteOnSandboxTermination": False,
            },
        }
    ]
    assert bodies[0]["env"] == {"HOME": "/home/kai"}


@pytest.mark.asyncio
async def test_connect_verifies_owner_and_does_not_create_another_instance() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        assert request.method == "GET"
        return httpx.Response(
            200,
            json={
                "metadata": {"harness.tenant": "tenant-a", "harness.computer": "computer-a"},
                "status": {"state": "Running"},
            },
        )

    client = OpenSandboxClient(
        api_url="https://sandbox.example", api_key="key", transport=httpx.MockTransport(handler)
    )
    try:
        remote = await client.connect(
            "instance-a",
            expected_metadata={"harness.tenant": "tenant-a", "harness.computer": "computer-a"},
        )
        assert remote.id == "instance-a"
        with pytest.raises(ValueError, match="ownership"):
            await client.connect("instance-a", expected_metadata={"harness.tenant": "tenant-b"})
    finally:
        await client.aclose()
    assert set(seen) == {"GET"}


@pytest.mark.asyncio
async def test_failed_reconnection_never_discards_persistent_environment() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.method)
        return httpx.Response(
            200,
            json={"metadata": {"harness.computer": "computer-a"}, "status": {"state": "Failed"}},
        )

    client = OpenSandboxClient(
        api_url="https://sandbox.example", api_key="key", transport=httpx.MockTransport(handler)
    )
    try:
        with pytest.raises(RuntimeError, match="became Failed"):
            await client.connect("instance-a", expected_metadata={"harness.computer": "computer-a"})
    finally:
        await client.aclose()
    assert "DELETE" not in seen


@pytest.mark.asyncio
async def test_renewal_requires_platform_confirmation_and_keeps_credentials_server_side() -> None:
    target = datetime.now(UTC) + timedelta(hours=1)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.headers["OPEN-SANDBOX-API-KEY"] == "key"
        assert request.url.path == "/v1/sandboxes/instance-a/renew-expiration"
        assert json.loads(request.content) == {"expiresAt": target.isoformat()}
        return httpx.Response(
            200,
            json={
                "expiresAt": (target if calls == 1 else target - timedelta(seconds=60)).isoformat()
            },
        )

    client = OpenSandboxClient(
        api_url="https://sandbox.example", api_key="key", transport=httpx.MockTransport(handler)
    )
    try:
        assert await client.renew_expiration("instance-a", target) == target
        with pytest.raises(RuntimeError, match="did not confirm"):
            await client.renew_expiration("instance-a", target)
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_invalid_reconnect_and_renewal_never_reach_network() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("invalid request reached lifecycle API")

    client = OpenSandboxClient(
        api_url="https://sandbox.example", api_key="key", transport=httpx.MockTransport(handler)
    )
    try:
        for identifier in ["../another", "id/proxy/22", "bad?query", ""]:
            with pytest.raises(ValueError):
                await client.connect(identifier, expected_metadata={"owner": "a"})
        with pytest.raises(ValueError):
            await client.connect("instance-a", expected_metadata={})
        with pytest.raises(ValueError):
            await client.renew_expiration("instance-a", datetime.now(UTC) - timedelta(hours=1))
        with pytest.raises(ValueError):
            await client.renew_expiration("instance-a", datetime(2026, 1, 1))
    finally:
        await client.aclose()
