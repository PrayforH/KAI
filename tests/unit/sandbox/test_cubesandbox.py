import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from e2b.connection_config import ConnectionConfig
from packaging.version import Version

from harness.config import Settings
from harness.runtime.codex_app_server import CodexAppServerOptions, DaytonaCodexAppServerProcess
from harness.sandbox.cubesandbox import (
    CubeAsyncSandbox,
    SdkCubeSandboxClient,
    build_cubesandbox_provider,
)
from harness.sandbox.e2b import E2BSandboxProvider
from tests.unit.sandbox.test_e2b import FakeClient, run


@pytest.mark.asyncio
async def test_cube_routes_each_instance_without_leaking_api_credential() -> None:
    config = ConnectionConfig(
        api_url="http://cube-api:13000",
        api_headers={"Authorization": "Bearer management-secret"},
        domain="cube.app",
        sandbox_url="http://cube-proxy:80",
        extra_sandbox_headers={"X-Access-Token": "workload-token"},
    )
    instances = [
        CubeAsyncSandbox(
            sandbox_id=identifier,
            sandbox_domain="ignored.upstream.domain",
            envd_version=Version("0.4.0"),
            envd_access_token="workload-token",
            traffic_access_token=None,
            connection_config=config,
        )
        for identifier in ("first", "second")
    ]
    for identifier, sandbox in zip(("first", "second"), instances, strict=True):
        assert sandbox.envd_api_url == "http://cube-proxy:80"
        headers = sandbox.connection_config.sandbox_headers
        assert headers["Host"] == f"49983-{identifier}.cube.app"
        assert headers["X-Access-Token"] == "workload-token"
        assert "Authorization" not in headers
        assert "management-secret" not in str(headers)
        assert sandbox.connection_config.headers["Authorization"] == "Bearer management-secret"
    assert "Host" not in config.sandbox_headers


@pytest.mark.asyncio
@pytest.mark.parametrize("credential", ["cube-token", "Bearer cube-token"])
async def test_cube_auth_and_lifecycle_parameters(
    monkeypatch: pytest.MonkeyPatch, credential: str
) -> None:
    requests = []
    original_client = httpx.AsyncClient

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            201,
            json={
                "sandboxID": "cube-created",
                "envdVersion": "0.4.0",
                "domain": "cube.app",
            },
        )

    def client_factory(**kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(respond))
        return original_client(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    client = SdkCubeSandboxClient(
        api_url="http://cube-api:13000",
        api_key=credential,
        proxy_url="http://cube-proxy:80",
        domain="cube.app",
    )
    sandbox = await client.create(
        template="template-id",
        timeout=600,
        allow_internet_access=False,
        metadata={"harness.tenant": "tenant-a"},
        network={"allow_out": ["pypi.org"], "deny_out": ["0.0.0.0/0"]},
        volume_mounts={"/data": "team-data"},
    )
    assert sandbox.id == "cube-created"
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == "http://cube-api:13000/sandboxes"
    assert request.headers["Authorization"] == "Bearer cube-token"
    body = json.loads(request.content)
    assert body["templateID"] == "template-id"
    assert body["secure"] is True
    assert body["allow_internet_access"] is False
    assert body["metadata"] == {"harness.tenant": "tenant-a"}
    assert body["network"] == {"allowOut": ["pypi.org"], "denyOut": ["0.0.0.0/0"]}
    assert body["volumeMounts"] == [{"path": "/data", "name": "team-data"}]


@pytest.mark.asyncio
async def test_cube_reconnect_uses_v1_and_preserves_instance_routing(monkeypatch):
    requests = []
    original_client = httpx.AsyncClient

    def respond(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "sandboxID": "kept-instance",
                "envdVersion": "0.4.0",
                "envdAccessToken": "instance-secret",
            },
        )

    def client_factory(**kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(respond))
        return original_client(**kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client_factory)
    client = SdkCubeSandboxClient(
        api_url="http://api", api_key="key", proxy_url="http://proxy", domain="cube.app"
    )
    sandbox = await client.attach("kept-instance")
    assert sandbox.id == "kept-instance"
    assert len(requests) == 1
    assert str(requests[0].url) == "http://api/sandboxes/kept-instance/connect"
    assert requests[0].method == "POST"
    assert json.loads(requests[0].content) == {}
    config = sandbox._sandbox.connection_config
    assert config.sandbox_headers["Host"] == "49983-kept-instance.cube.app"
    assert config.sandbox_headers["X-Access-Token"] == "instance-secret"
    assert "Authorization" not in config.sandbox_headers


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 405, 503])
async def test_cube_creation_errors_propagate_without_fallback(monkeypatch, status):
    original_client = httpx.AsyncClient
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(status, text="control plane unavailable")

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original_client(**kwargs, transport=httpx.MockTransport(respond)),
    )
    client = SdkCubeSandboxClient(
        api_url="http://api", api_key="key", proxy_url="http://proxy", domain="cube.app"
    )
    with pytest.raises(httpx.HTTPStatusError):
        await client.create(template="tpl", timeout=60, allow_internet_access=True, metadata={})
    assert len(requests) == 1


def test_cube_requires_explicit_endpoints_and_key() -> None:
    with pytest.raises(ValueError, match="TEMPLATE"):
        build_cubesandbox_provider(Settings(cubesandbox_template=""))
    with pytest.raises(ValueError, match="API_KEY"):
        SdkCubeSandboxClient(
            api_url="http://api", api_key="", proxy_url="http://proxy", domain="cube.app"
        )


@pytest.mark.asyncio
async def test_cube_deferred_skips_cli_and_remote_codex_uses_process_transport() -> None:
    client = FakeClient()
    provider = E2BSandboxProvider(
        client=client,
        provider_name="cubesandbox",
        codex_cli_path="/opt/codex",
        codex_cli_version="0.149.0",
        codex_cli_sha256="sha",
    )
    handle = await provider.provision(run())
    try:
        await provider.prepare(handle.model_copy(update={"deferred_tool_execution": True}))
        assert client.sandbox.ensured_cli is None
        assert handle.provider == "cubesandbox"
        assert handle.runtime_transport_factory is not None
        process = handle.runtime_transport_factory(
            CodexAppServerOptions(codex_path=Path("codex"), working_directory=handle.path)
        )
        assert isinstance(process, DaytonaCodexAppServerProcess)
    finally:
        await provider.destroy(handle)


@pytest.mark.asyncio
async def test_command_timeout_preserves_watcher_and_kills_remote_process() -> None:
    import asyncio
    from types import SimpleNamespace
    from typing import Any, cast

    from harness.sandbox.e2b import SdkE2BRemoteSession

    exited = asyncio.Event()

    async def wait() -> SimpleNamespace:
        await exited.wait()
        return SimpleNamespace(exit_code=137)

    async def kill() -> None:
        exited.set()

    process = SimpleNamespace(wait=wait, kill=AsyncMock(side_effect=kill))
    sandbox = SimpleNamespace(commands=SimpleNamespace(run=AsyncMock(return_value=process)))
    session = SdkE2BRemoteSession(cast(Any, sandbox))
    await session.start(["sleep", "20"], "/workspace", {})
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(session.wait(), timeout=0.01)
    await session.terminate()
    process.kill.assert_awaited_once()
    assert await session.wait() == 137
