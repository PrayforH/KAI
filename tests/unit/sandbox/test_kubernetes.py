import io
import tarfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from harness.core.models import Run, RunStatus
from harness.sandbox.base import SandboxIsolation
from harness.sandbox.kubernetes import (
    KubectlRemoteSession,
    KubernetesPodRecord,
    KubernetesSandboxProvider,
    build_run_resources,
)
from harness.studio.catalog import default_capability_catalog


def run() -> Run:
    now = datetime(2026, 7, 16, tzinfo=UTC)
    return Run(
        run_id="run-a",
        session_id="session-a",
        tenant_id="tenant-a",
        status=RunStatus.PROVISIONING,
        idempotency_key="gvisor",
        fencing_token=3,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_kubectl_session_frames_secrets_outside_process_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[str] = []

    class Stdin:
        def __init__(self) -> None:
            self.content = bytearray()

        def write(self, data: bytes) -> None:
            self.content.extend(data)

        async def drain(self) -> None:
            return None

        def is_closing(self) -> bool:
            return False

        def close(self) -> None:
            return None

    class Process:
        def __init__(self) -> None:
            self.stdin = Stdin()
            self.stdout = None
            self.stderr = None
            self.returncode = 0

        async def wait(self) -> int:
            return 0

    process = Process()

    async def create(*arguments: str, **_kwargs: object) -> Process:
        captured.extend(arguments)
        return process

    monkeypatch.setattr("asyncio.create_subprocess_exec", create)
    session = KubectlRemoteSession(
        kubectl_argv=("kubectl",), namespace="sandboxes", pod_name="run-a"
    )

    await session.start(
        ["claude", "--system-prompt", "private instructions"],
        "/workspace",
        {"ANTHROPIC_AUTH_TOKEN": "private-token"},
    )
    await session.end_input()

    command = " ".join(captured)
    assert "private-token" not in command
    assert "private instructions" not in command
    assert b"private-token" not in process.stdin.content
    assert b"private instructions" not in process.stdin.content


class FakeSession:
    def __init__(self, stdout: bytes, *, exit_code: int = 0) -> None:
        self._stdout = [stdout, None]
        self._stderr = [None]
        self._exit_code = exit_code
        self.started: tuple[list[str], str, dict[str, str]] | None = None
        self.ended = False
        self.terminated = False

    async def stage_config(
        self, _remote_directory: str, _files: dict[str, bytes]
    ) -> None:
        return

    async def start(self, argv: list[str], cwd: str, env: dict[str, str]) -> None:
        self.started = (argv, cwd, env)

    async def write(self, data: str) -> None:
        del data

    async def end_input(self) -> None:
        self.ended = True

    async def read_stdout(self) -> bytes | None:
        return self._stdout.pop(0)

    async def read_stderr(self) -> bytes | None:
        return self._stderr.pop(0)

    async def wait(self) -> int:
        return self._exit_code

    async def terminate(self) -> None:
        self.terminated = True


class FakeClient:
    def __init__(self) -> None:
        self.resources: Mapping[str, object] | None = None
        self.waited: list[tuple[str, float]] = []
        self.uploaded: bytes | None = None
        self.downloaded = self.archive({"outputs/report.md": b"report"})
        self.deleted: list[tuple[str, str]] = []
        self.sessions: list[FakeSession] = []
        self.records: Sequence[KubernetesPodRecord] = ()
        self.fail_ready = False

    @staticmethod
    def archive(files: Mapping[str, bytes], *, symlink: bool = False) -> bytes:
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            for name, content in files.items():
                member = tarfile.TarInfo(name)
                if symlink:
                    member.type = tarfile.SYMTYPE
                    member.linkname = "../../escape"
                    archive.addfile(member)
                else:
                    member.size = len(content)
                    archive.addfile(member, io.BytesIO(content))
        return buffer.getvalue()

    async def create(self, resources: Mapping[str, object]) -> None:
        self.resources = resources

    async def wait_ready(self, pod_name: str, *, timeout_seconds: float) -> None:
        self.waited.append((pod_name, timeout_seconds))
        if self.fail_ready:
            raise RuntimeError("cluster unavailable")

    async def upload_archive(
        self, pod_name: str, destination: str, content: bytes
    ) -> None:
        del pod_name, destination
        self.uploaded = content

    async def download_archive(
        self, pod_name: str, source: str, *, max_bytes: int
    ) -> bytes:
        del pod_name, source, max_bytes
        return self.downloaded

    def remote_session(self, pod_name: str) -> FakeSession:
        del pod_name
        session = FakeSession(b"2.1.206 (Claude Code)\n")
        self.sessions.append(session)
        return session

    async def delete(self, pod_name: str, network_policy_name: str) -> None:
        self.deleted.append((pod_name, network_policy_name))

    async def list_managed_pods(self) -> Sequence[KubernetesPodRecord]:
        return self.records


def provider(client: FakeClient, root: Path, **overrides: object) -> KubernetesSandboxProvider:
    values: dict[str, object] = {
        "client": client,
        "namespace": "harness-sandboxes",
        "image": "registry.example/harness@sha256:" + "a" * 64,
        "local_root": root,
        "egress_gateway_namespace": "harness-system",
        "egress_gateway_selector": {
            "app.kubernetes.io/name": "harness-egress-proxy"
        },
        "egress_proxy_url": "http://harness-egress-proxy.harness-system.svc:3128",
        "clock": lambda: datetime(2026, 7, 16, tzinfo=UTC),
    }
    values.update(overrides)
    return KubernetesSandboxProvider(**values)  # pyright: ignore[reportArgumentType]


def test_run_resources_enforce_gvisor_and_least_privilege() -> None:
    manifest = build_run_resources(
        pod_name="harness-run-a",
        namespace="harness-sandboxes",
        run=run(),
        image="registry.example/harness@sha256:" + "a" * 64,
        runtime_class_name="gvisor",
        service_account_name="harness-sandbox",
        cpu_millis=2000,
        memory_mib=4096,
        disk_mib=20480,
        expires_at=datetime(2026, 7, 16, 1, tzinfo=UTC),
        egress_gateway_namespace="harness-system",
        egress_gateway_selector={"app": "proxy"},
        egress_gateway_port=3128,
        egress_proxy_url="http://proxy:3128",
        dns_namespace="kube-system",
    )
    items = cast(list[dict[str, Any]], manifest["items"])
    assert isinstance(items, list)
    pod = next(item for item in items if item["kind"] == "Pod")
    policy = next(item for item in items if item["kind"] == "NetworkPolicy")
    assert items[0]["kind"] == "NetworkPolicy"
    spec = cast(dict[str, Any], pod["spec"])
    container = cast(list[dict[str, Any]], spec["containers"])[0]

    assert spec["runtimeClassName"] == "gvisor"
    assert spec["automountServiceAccountToken"] is False
    assert spec.get("hostNetwork", False) is False
    assert "hostPath" not in str(spec)
    assert container["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "privileged": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }
    policy_spec = cast(dict[str, Any], policy["spec"])
    assert policy_spec["ingress"] == []
    egress = cast(list[dict[str, Any]], policy_spec["egress"])
    assert len(egress) == 2
    assert "0.0.0.0/0" not in str(egress)


def test_catalog_exposes_versioned_gvisor_execution_profile() -> None:
    profile = next(
        item
        for item in default_capability_catalog().execution_profiles
        if item.profile_id == "gvisor-production"
    )

    assert profile.sandbox_provider == "gvisor"
    assert profile.production_allowed is True
    assert profile.provider_config_reference == "kubernetes-gvisor-managed"
    assert profile.network_policy_id == "registered-mcp-only"


@pytest.mark.asyncio
async def test_provider_syncs_workspace_executes_and_deletes_owned_pod(
    tmp_path: Path,
) -> None:
    client = FakeClient()
    sandbox = provider(client, tmp_path)
    handle = await sandbox.provision(run())
    (handle.path / "inputs").mkdir()
    (handle.path / "inputs/facts.txt").write_text("facts")

    await sandbox.prepare(handle)
    await sandbox.collect(handle)
    report = (handle.path / "outputs/report.md").read_bytes()
    await sandbox.destroy(handle)

    assert handle.provider == "kubernetes-gvisor"
    assert handle.isolation_level is SandboxIsolation.CONTAINER
    assert handle.remote_workspace == "/workspace"
    assert client.resources is not None
    assert client.uploaded is not None
    with tarfile.open(fileobj=io.BytesIO(client.uploaded)) as uploaded:
        assert uploaded.extractfile("inputs/facts.txt").read() == b"facts"  # type: ignore[union-attr]
    assert report == b"report"
    assert len(client.deleted) == 1
    assert not handle.path.exists()


@pytest.mark.asyncio
async def test_collection_rejects_symlink_and_size_escape(tmp_path: Path) -> None:
    client = FakeClient()
    sandbox = provider(client, tmp_path, max_collect_bytes=4)
    handle = await sandbox.provision(run())
    client.downloaded = client.archive({"escape": b""}, symlink=True)
    with pytest.raises(ValueError, match="unsafe"):
        await sandbox.collect(handle)
    client.downloaded = client.archive({"too-large": b"12345"})
    with pytest.raises(ValueError, match="size limit"):
        await sandbox.collect(handle)
    await sandbox.destroy(handle)


@pytest.mark.asyncio
async def test_collect_overwrites_read_only_staged_input(tmp_path: Path) -> None:
    client = FakeClient()
    sandbox = provider(client, tmp_path)
    handle = await sandbox.provision(run())
    staged_input = handle.path / "inputs" / "original" / "工作簿1.xlsx"
    staged_input.parent.mkdir(parents=True)
    staged_input.write_bytes(b"staged")
    staged_input.chmod(0o444)
    client.downloaded = client.archive({"inputs/original/工作簿1.xlsx": b"remote"})

    await sandbox.collect(handle)

    assert staged_input.read_bytes() == b"remote"
    assert staged_input.stat().st_mode & 0o400
    await sandbox.destroy(handle)


@pytest.mark.asyncio
async def test_ready_failure_cleans_up_and_never_returns_local(tmp_path: Path) -> None:
    client = FakeClient()
    client.fail_ready = True
    sandbox = provider(client, tmp_path)

    with pytest.raises(RuntimeError, match="cluster unavailable"):
        await sandbox.provision(run())

    assert len(client.deleted) == 1
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_reaper_deletes_only_expired_managed_pods(tmp_path: Path) -> None:
    client = FakeClient()
    now = datetime(2026, 7, 16, tzinfo=UTC)
    client.records = (
        KubernetesPodRecord("expired", now - timedelta(seconds=1)),
        KubernetesPodRecord("active", now + timedelta(seconds=1)),
    )
    sandbox = provider(client, tmp_path, clock=lambda: now)

    assert await sandbox.reap_expired() == 1
    assert client.deleted == [("expired", "expired-egress")]


def test_provider_requires_digest_pinned_image_and_registered_gateway(
    tmp_path: Path,
) -> None:
    client = FakeClient()
    with pytest.raises(ValueError, match="pinned by digest"):
        provider(client, tmp_path, image="registry.example/harness:latest")
    with pytest.raises(ValueError, match="registered egress gateway"):
        build_run_resources(
            pod_name="run",
            namespace="ns",
            run=run(),
            image="image",
            runtime_class_name="gvisor",
            service_account_name="sandbox",
            cpu_millis=1,
            memory_mib=1,
            disk_mib=1,
            expires_at=datetime.now(UTC),
            egress_gateway_namespace="system",
            egress_gateway_selector={},
            egress_gateway_port=3128,
            egress_proxy_url="",
            dns_namespace="kube-system",
        )
