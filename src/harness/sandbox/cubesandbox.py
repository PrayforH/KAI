"""CubeSandbox's E2B-compatible API with explicit private proxy routing."""

import asyncio
import json
import shlex
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Unpack, cast
from urllib.parse import urlsplit

import httpx
from e2b import AsyncSandbox
from e2b.connection_config import ApiParams, ConnectionConfig
from e2b.sandbox.main import SandboxOpts  # pyright: ignore[reportMissingTypeStubs]

from harness.config import Settings
from harness.sandbox.base import SandboxResourceUsage
from harness.sandbox.claude_cli import (
    banner_matches,
    bundled_cli_path,
    version_pin,
    version_text,
)
from harness.sandbox.e2b import (
    _LIST_SCAN_LIMIT,
    _MANAGED_RUN_KEY,
    _MANAGED_TENANT_KEY,
    E2BRemoteSandbox,
    E2BSandboxProvider,
    SdkE2BRemoteSandbox,
    _platform_logs,
)


class CubeAsyncSandbox(AsyncSandbox):
    """Keep API credentials on the API and route envd by its per-sandbox Host."""

    def __init__(self, **opts: Unpack[SandboxOpts]) -> None:
        config = opts["connection_config"]
        domain = config.domain
        sandbox_id = opts["sandbox_id"]
        if not domain or any(char in domain for char in "/:\r\n "):
            raise ValueError("CubeSandbox domain must be a DNS name")
        if not sandbox_id or any(char in sandbox_id for char in "/:\r\n "):
            raise ValueError("CubeSandbox returned an invalid sandbox ID")
        opts["connection_config"] = ConnectionConfig(
            **cast(ApiParams, cast(Any, config).get_api_params()),
            extra_sandbox_headers={
                **config.sandbox_headers,
                "Host": f"{ConnectionConfig.envd_port}-{sandbox_id}.{domain}",
            },
        )
        super().__init__(**opts)


class CubeRemoteSandbox(SdkE2BRemoteSandbox):
    """Bootstrap from the Worker's pinned Linux SDK binary on private networks."""

    async def ensure_claude_cli(self, *, version: str, path: str) -> None:
        await self._ensure_binary(bundled_cli_path(), path, version_pin(version))

    async def ensure_codex_cli(self, *, version: str, path: str) -> None:
        import shutil

        source = shutil.which("codex")
        if source is None:
            raise RuntimeError("CubeSandbox Codex requires the pinned CLI in the Linux Worker")
        await self._ensure_binary(Path(source).resolve(), path, f"codex-cli {version}")

    async def _ensure_binary(self, bundled: Path, path: str, expected: str | None) -> None:
        try:
            check = await self._sandbox.commands.run(f"{shlex.quote(path)} --version")
            if banner_matches(version_text(check.stdout, check.stderr), expected):
                return
        except Exception:  # noqa: BLE001 - missing CLI is an expected template cache miss
            pass
        with bundled.open("rb") as source:
            if source.read(4) != b"\x7fELF":
                raise RuntimeError("CubeSandbox remote CLI requires a Linux Worker or prebuilt CLI")
        process = await asyncio.create_subprocess_exec(
            str(bundled),
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=15)
        if process.returncode != 0 or not banner_matches(
            version_text(stdout.decode(), ""), expected
        ):
            raise RuntimeError("Worker CLI does not match the pinned version")
        await self.create_folder(str(Path(path).parent))
        await self.upload(path, await asyncio.to_thread(bundled.read_bytes))
        await self._sandbox.commands.run(f"chmod 755 -- {shlex.quote(path)}")
        verified = await self._sandbox.commands.run(f"{shlex.quote(path)} --version")
        if not banner_matches(version_text(verified.stdout, verified.stderr), expected):
            raise RuntimeError("CubeSandbox CLI version verification failed")


class SdkCubeSandboxClient:
    def __init__(self, *, api_url: str, api_key: str, proxy_url: str, domain: str) -> None:
        token = api_key.strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        if not token or "\n" in token or "\r" in token:
            raise ValueError("HARNESS_CUBESANDBOX_API_KEY is required")
        if not domain or any(char in domain for char in "/:\r\n "):
            raise ValueError("CubeSandbox domain must be a DNS name")
        for name, url in (("API", api_url), ("proxy", proxy_url)):
            parsed = urlsplit(url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError(f"CubeSandbox {name} URL must be an HTTP(S) endpoint")
        self._api_url = api_url.rstrip("/")
        self._api_key = token
        self._proxy_url = proxy_url.rstrip("/")
        self._domain = domain

    async def create(
        self,
        *,
        template: str,
        timeout: int,
        allow_internet_access: bool,
        metadata: Mapping[str, str],
        network: Mapping[str, object] | None = None,
        volume_mounts: Mapping[str, str] | None = None,
    ) -> E2BRemoteSandbox:
        sandbox = await CubeAsyncSandbox.create(
            template=template,
            timeout=timeout,
            secure=True,
            allow_internet_access=allow_internet_access,
            metadata=dict(metadata),
            api_key=self._api_key,
            validate_api_key=False,
            api_headers={"Authorization": f"Bearer {self._api_key}"},
            api_url=self._api_url,
            domain=self._domain,
            sandbox_url=self._proxy_url,
            request_timeout=30,
            debug=False,
            network=dict(network) if network is not None else None,
            volume_mounts=dict(volume_mounts) if volume_mounts else None,
        )
        return CubeRemoteSandbox(sandbox)

    def _control_plane(self) -> dict[str, object]:
        """Connection parameters for Cube API calls that are not data-plane."""

        return {
            "api_key": self._api_key,
            "validate_api_key": False,
            "api_headers": {"Authorization": f"Bearer {self._api_key}"},
            "api_url": self._api_url,
            "domain": self._domain,
            "request_timeout": 30,
            "debug": False,
        }

    async def list_managed(self) -> list[tuple[str, Mapping[str, str]]]:
        entries: list[tuple[str, Mapping[str, str]]] = []
        paginator = CubeAsyncSandbox.list(**cast(Any, self._control_plane()))
        while paginator.has_next and len(entries) < _LIST_SCAN_LIMIT:
            for info in await paginator.next_items():
                metadata = dict(info.metadata or {})
                if _MANAGED_TENANT_KEY in metadata and _MANAGED_RUN_KEY in metadata:
                    entries.append((info.sandbox_id, metadata))
        return entries

    async def kill_sandbox(self, sandbox_id: str) -> None:
        await CubeAsyncSandbox.kill(sandbox_id, **cast(Any, self._control_plane()))

    async def attach(self, sandbox_id: str) -> E2BRemoteSandbox:
        params = self._control_plane()
        params["sandbox_url"] = self._proxy_url
        sandbox = await CubeAsyncSandbox.connect(sandbox_id, **cast(Any, params))
        return CubeRemoteSandbox(sandbox)

    async def template_state(self, reference: str) -> str | None:
        """Resolve a template ID or alias to its platform status.

        CubeSandbox accepts either form at create time, so an operator switching
        from an ID to an alias cannot tell them apart from the config alone;
        reading the catalogue is what makes a wrong or failed template visible
        before a Run depends on it.
        """

        token = str(reference).strip()
        if not token:
            return "MISSING"
        async with httpx.AsyncClient(
            base_url=self._api_url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            timeout=30,
        ) as client:
            response = await client.get("/templates")
            if response.status_code >= 400:
                return None
            templates = response.json()
        if not isinstance(templates, list):
            return None
        for template in templates:
            if not isinstance(template, Mapping):
                continue
            aliases = template.get("aliases") or ()
            if template.get("templateID") == token or token in aliases:
                return str(template.get("status") or "UNKNOWN")
        return "MISSING"

    async def snapshot(self, sandbox_id: str, name: str | None = None) -> str:
        params = self._control_plane()
        info = await CubeAsyncSandbox.create_snapshot(
            sandbox_id, name=name, **cast(Any, params)
        )
        return str(info.snapshot_id)

    async def pause_sandbox(self, sandbox_id: str) -> None:
        await CubeAsyncSandbox.pause(sandbox_id, **cast(Any, self._control_plane()))

    async def logs(self, sandbox_id: str, limit: int) -> tuple[str, ...]:
        # Verified against the 174-side deployment: /sandboxes/{id}/logs answers
        # with {"logs": [{"timestamp", "line"}]} and authenticates by Bearer.
        return await _platform_logs(
            self._api_url,
            {"Authorization": f"Bearer {self._api_key}"},
            sandbox_id,
            limit,
            path="/sandboxes/{sandbox_id}/logs",
        )

    async def metrics(self, sandbox_id: str) -> SandboxResourceUsage | None:
        # CubeSandbox does not implement per-sandbox metrics: both the v1 and v2
        # routes answer 404 and the SDK raises on the empty body. Report nothing
        # rather than zeroes.
        del sandbox_id
        return None

    async def envd_version(self, sandbox_id: str) -> str | None:
        params = self._control_plane()
        try:
            info = await CubeAsyncSandbox.get_info(sandbox_id, **cast(Any, params))
        except Exception:  # noqa: BLE001 - an absent version is not a failure here
            return None
        version = getattr(info, "envd_version", None)
        return str(version) if version else None


def _volume_mounts(raw: str) -> dict[str, str]:
    """Parse the configured mount path to volume-name mapping."""

    if not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        raise ValueError("HARNESS_CUBESANDBOX_VOLUME_MOUNTS must be a JSON object") from None
    if not isinstance(parsed, dict):
        raise ValueError("HARNESS_CUBESANDBOX_VOLUME_MOUNTS must be a JSON object")
    mounts: dict[str, str] = {}
    for path, volume in parsed.items():
        if not isinstance(path, str) or not path.startswith("/"):
            raise ValueError("CubeSandbox volume mount paths must be absolute")
        if not isinstance(volume, str) or not volume.strip():
            raise ValueError("CubeSandbox volume names must be non-empty strings")
        mounts[path] = volume
    return mounts


def build_cubesandbox_provider(settings: Settings) -> E2BSandboxProvider:
    if not settings.cubesandbox_template.strip():
        raise ValueError("HARNESS_CUBESANDBOX_TEMPLATE is required")
    return E2BSandboxProvider(
        client=SdkCubeSandboxClient(
            api_url=settings.cubesandbox_api_url,
            api_key=settings.cubesandbox_api_key.get_secret_value(),
            proxy_url=settings.cubesandbox_proxy_url,
            domain=settings.cubesandbox_domain,
        ),
        provider_name="cubesandbox",
        template=settings.cubesandbox_template,
        timeout_seconds=settings.cubesandbox_timeout_seconds,
        allow_internet_access=settings.cubesandbox_allow_internet_access,
        remote_workspace_root=settings.cubesandbox_remote_workspace_root,
        cli_version=settings.cubesandbox_claude_cli_version,
        cli_path=settings.cubesandbox_claude_cli_path,
        codex_cli_path=settings.cubesandbox_codex_cli_path,
        codex_cli_version=settings.daytona_codex_cli_version,
        codex_cli_sha256=settings.daytona_codex_cli_sha256,
        max_collect_bytes=settings.workspace_archive_max_bytes,
        max_collect_members=settings.workspace_archive_max_members,
        idle_policy=settings.cubesandbox_idle_policy,
        volume_mounts=_volume_mounts(settings.cubesandbox_volume_mounts),
    )
