from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from claude_agent_sdk import ClaudeAgentOptions

from harness.core.models import Run, RunStatus
from harness.sandbox.base import SandboxEgress, SandboxIsolation, SandboxResourceUsage
from harness.sandbox.e2b import (
    E2BSandboxProvider,
    SdkE2BClient,
    SdkE2BRemoteSandbox,
    SdkE2BRemoteSession,
    _parse_log_payload,
)


class FakeRemoteSession:
    def __init__(self) -> None:
        self.started: tuple[list[str], str, dict[str, str]] | None = None
        self.terminated = False
        self.stdout = [b"connected\n", None]
        self.stderr = [None]

    async def stage_config(self, remote_directory: str, files: dict[str, bytes]) -> None:
        del remote_directory, files

    async def start(self, argv: list[str], cwd: str, env: dict[str, str]) -> None:
        self.started = (argv, cwd, env)

    async def write(self, data: str) -> None:
        del data

    async def end_input(self) -> None:
        return

    async def read_stdout(self) -> bytes | None:
        # Exhausting the queue is the stream's EOF, as it is remotely.
        return self.stdout.pop(0) if self.stdout else None

    async def read_stderr(self) -> bytes | None:
        return self.stderr.pop(0) if self.stderr else None

    async def wait(self) -> int:
        return 0

    async def terminate(self) -> None:
        self.terminated = True


class FakeSandbox:
    def __init__(self) -> None:
        self.id = "e2b-sandbox-a"
        self.ensured_cli: tuple[str, str] | None = None
        self.folders: list[str] = []
        self.uploads: dict[str, bytes] = {}
        self.remote_files: dict[str, bytes] = {}
        self.killed = False
        self.renewals: list[int] = []
        self.renew_error: Exception | None = None
        self.removed: list[str] = []
        self.remove_failure: Exception | None = None
        self.ping_ok = True
        self.snapshot_names: list[str | None] = []
        self.paused = False
        self.pause_failure: Exception | None = None
        self.session = FakeRemoteSession()

    async def ensure_claude_cli(self, *, version: str, path: str) -> None:
        self.ensured_cli = (version, path)

    async def create_folder(self, path: str) -> None:
        self.folders.append(path)

    async def upload(self, remote_path: str, content: bytes) -> None:
        self.uploads[remote_path] = content

    async def list_files(self, remote_path: str) -> list[tuple[str, bool, int | None]]:
        return [
            (path, False, len(content))
            for path, content in self.remote_files.items()
            if path.startswith(remote_path + "/")
        ]

    async def download(self, remote_path: str) -> bytes:
        return self.remote_files[remote_path]

    async def kill(self) -> None:
        self.killed = True

    async def renew(self, timeout_seconds: int) -> None:
        self.renewals.append(timeout_seconds)

    async def remove_tree(self, path: str) -> None:
        if self.remove_failure is not None:
            raise self.remove_failure
        self.removed.append(path)

    async def ping(self) -> bool:
        return self.ping_ok

    async def snapshot(self, name: str | None = None) -> str:
        self.snapshot_names.append(name)
        return "snap-new"

    async def pause(self) -> None:
        if self.pause_failure is not None:
            raise self.pause_failure
        self.paused = True

    def remote_session(self) -> FakeRemoteSession:
        return self.session


class FakeClient:
    def __init__(self) -> None:
        self.sandbox = FakeSandbox()
        self.created: dict[str, Any] | None = None
        self.managed: list[tuple[str, dict[str, str]]] = []
        self.killed: list[str] = []
        self.kill_failures: set[str] = set()
        self.attached: list[str] = []
        self.template_queries: list[str] = []
        self.template_status: str | None = "READY"
        self.snapshots: list[tuple[str, str | None]] = []
        self.log_queries: list[tuple[str, int]] = []
        self.log_lines: tuple[str, ...] = ()
        self.metric_queries: list[str] = []
        self.metric_sample: SandboxResourceUsage | None = None
        self.envd_queries: list[str] = []
        self.envd: str | None = "0.5.11"
        self.warm_sandbox = FakeSandbox()
        self.create_calls = 0

    async def create(self, **parameters: Any) -> FakeSandbox:
        self.created = parameters
        self.create_calls += 1
        return self.sandbox

    async def list_managed(self) -> list[tuple[str, dict[str, str]]]:
        return list(self.managed)

    async def attach(self, sandbox_id: str) -> FakeSandbox:
        self.attached.append(sandbox_id)
        return self.warm_sandbox

    async def template_state(self, reference: str) -> str | None:
        self.template_queries.append(reference)
        return self.template_status

    async def snapshot(self, sandbox_id: str, name: str | None = None) -> str:
        self.snapshots.append((sandbox_id, name))
        return "snap-new"

    async def logs(self, sandbox_id: str, limit: int) -> tuple[str, ...]:
        self.log_queries.append((sandbox_id, limit))
        return self.log_lines[-limit:]

    async def metrics(self, sandbox_id: str) -> SandboxResourceUsage | None:
        self.metric_queries.append(sandbox_id)
        return self.metric_sample

    async def envd_version(self, sandbox_id: str) -> str | None:
        self.envd_queries.append(sandbox_id)
        return self.envd

    async def kill_sandbox(self, sandbox_id: str) -> None:
        if sandbox_id in self.kill_failures:
            raise RuntimeError("platform refused teardown")
        self.killed.append(sandbox_id)


def run() -> Run:
    return Run(
        run_id="run-a",
        session_id="session-a",
        tenant_id="tenant-a",
        status=RunStatus.PROVISIONING,
        idempotency_key="e2b",
        created_at=datetime(2026, 7, 17, tzinfo=UTC),
        updated_at=datetime(2026, 7, 17, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_e2b_provider_stages_executes_collects_and_kills(tmp_path: Path) -> None:
    client = FakeClient()
    provider = E2BSandboxProvider(
        client=client,
        local_root=tmp_path,
        template="harness-template",
        timeout_seconds=900,
        allow_internet_access=True,
        remote_workspace_root="/home/user/harness",
    )
    handle = await provider.provision(run())
    (handle.path / "inputs").mkdir()
    (handle.path / "inputs" / "facts.txt").write_text("facts")

    await provider.prepare(handle)
    result = await provider.execute(
        handle,
        ("bash", "-lc", "echo connected"),
        environment={"SAFE_FLAG": "yes"},
    )
    client.sandbox.remote_files["/home/user/harness/run-a/outputs/report.md"] = b"report"
    await provider.collect(handle)
    options = ClaudeAgentOptions()
    assert handle.runtime_transport_factory is not None
    handle.runtime_transport_factory(options)
    collected_report = (handle.path / "outputs" / "report.md").read_bytes()
    await provider.destroy(handle)

    assert client.created == {
        "template": "harness-template",
        "timeout": 900,
        "allow_internet_access": True,
        "metadata": {
            "harness.tenant": "tenant-a",
            "harness.session": "session-a",
            "harness.run": "run-a",
        },
        "network": None,
        "volume_mounts": None,
    }
    assert handle.provider == "e2b"
    assert handle.isolation_level is SandboxIsolation.CONTAINER
    # No version pin by default: provisioning follows the CLI bundled with
    # claude-agent-sdk, which is the version the SDK is locked against.
    assert client.sandbox.ensured_cli == (
        "",
        "/home/user/.local/bin/claude",
    )
    assert client.sandbox.uploads["/home/user/harness/run-a/inputs/facts.txt"] == b"facts"
    assert result.stdout == "connected\n"
    assert client.sandbox.session.started == (
        ["bash", "-lc", "echo connected"],
        "/home/user/harness/run-a",
        {"SAFE_FLAG": "yes"},
    )
    assert collected_report == b"report"
    assert options.env["CLAUDE_CONFIG_DIR"].startswith("/home/user/harness/.claude-config/")
    assert client.sandbox.killed is True
    assert not handle.path.exists()


@pytest.mark.asyncio
async def test_e2b_collection_limits_are_enforced(tmp_path: Path) -> None:
    client = FakeClient()
    provider = E2BSandboxProvider(
        client=client,
        local_root=tmp_path,
        max_collect_bytes=3,
    )
    handle = await provider.provision(run())
    client.sandbox.remote_files["/home/user/harness/run-a/large.bin"] = b"1234"

    with pytest.raises(ValueError, match="collection size"):
        await provider.collect(handle)

    await provider.destroy(handle)


def managed_sandbox(
    sandbox_id: str, *, run_id: str, tenant_id: str = "tenant-a"
) -> tuple[str, dict[str, str]]:
    return (sandbox_id, {"harness.tenant": tenant_id, "harness.run": run_id})


def governed_provider(client: FakeClient, tmp_path: Path) -> E2BSandboxProvider:
    return E2BSandboxProvider(
        client=client,
        local_root=tmp_path,
        template="harness-template",
        timeout_seconds=900,
        allow_internet_access=True,
        remote_workspace_root="/home/user/harness",
    )


@pytest.mark.asyncio
async def test_reap_deletes_only_sandboxes_whose_run_is_gone(tmp_path: Path) -> None:
    """A finished or vanished Run must not keep holding a sandbox."""

    client = FakeClient()
    client.managed = [
        managed_sandbox("sbx-live", run_id="run-live"),
        managed_sandbox("sbx-done", run_id="run-done"),
        managed_sandbox("sbx-missing", run_id="run-missing"),
    ]
    provider = governed_provider(client, tmp_path)

    async def is_active(tenant_id: str, run_id: str) -> bool:
        del tenant_id
        return run_id == "run-live"

    provider.bind_run_liveness(is_active)
    assert await provider.reap_expired() == 2
    assert sorted(client.killed) == ["sbx-done", "sbx-missing"]


@pytest.mark.asyncio
async def test_reap_never_touches_a_run_that_is_still_active(tmp_path: Path) -> None:
    client = FakeClient()
    client.managed = [managed_sandbox("sbx-live", run_id="run-live")]
    provider = governed_provider(client, tmp_path)

    async def is_active(tenant_id: str, run_id: str) -> bool:
        del tenant_id, run_id
        return True

    provider.bind_run_liveness(is_active)
    assert await provider.reap_expired() == 0
    assert client.killed == []


@pytest.mark.asyncio
async def test_reap_is_off_without_a_bound_predicate(tmp_path: Path) -> None:
    """Deleting sandboxes must be opted into, not a default behaviour."""

    client = FakeClient()
    client.managed = [managed_sandbox("sbx-done", run_id="run-done")]
    provider = governed_provider(client, tmp_path)
    assert await provider.reap_expired() == 0
    assert client.killed == []


@pytest.mark.asyncio
async def test_reap_survives_one_failing_teardown(tmp_path: Path) -> None:
    client = FakeClient()
    client.managed = [
        managed_sandbox("sbx-bad", run_id="run-done"),
        managed_sandbox("sbx-good", run_id="run-done"),
    ]
    client.kill_failures = {"sbx-bad"}
    provider = governed_provider(client, tmp_path)

    async def is_active(tenant_id: str, run_id: str) -> bool:
        del tenant_id, run_id
        return False

    provider.bind_run_liveness(is_active)
    assert await provider.reap_expired() == 1
    assert client.killed == ["sbx-good"]


@pytest.mark.asyncio
async def test_active_count_reports_platform_sandboxes(tmp_path: Path) -> None:
    client = FakeClient()
    client.managed = [
        managed_sandbox("sbx-a", run_id="run-a"),
        managed_sandbox("sbx-b", run_id="run-b"),
    ]
    provider = governed_provider(client, tmp_path)
    assert await provider.active_count() == 2


def test_egress_network_translation_is_deny_by_default() -> None:
    """An allow list alone would not close a platform whose default is allow."""

    assert E2BSandboxProvider.egress_network(None) is None
    assert E2BSandboxProvider.egress_network(SandboxEgress()) is None
    assert E2BSandboxProvider.egress_network(SandboxEgress(deny_internet=True)) == {
        "deny_out": ["0.0.0.0/0"]
    }
    assert E2BSandboxProvider.egress_network(
        SandboxEgress(allow_hosts=("mcp.internal.example", "python.internal.example"))
    ) == {
        "deny_out": ["0.0.0.0/0"],
        "allow_out": ["mcp.internal.example", "python.internal.example"],
    }


@pytest.mark.asyncio
async def test_provision_with_egress_sends_the_policy_to_the_platform(tmp_path: Path) -> None:
    client = FakeClient()
    provider = governed_provider(client, tmp_path)
    await provider.provision_with_egress(
        run(), SandboxEgress(allow_hosts=("mcp.internal.example",))
    )
    assert client.created is not None
    assert client.created["network"] == {
        "deny_out": ["0.0.0.0/0"],
        "allow_out": ["mcp.internal.example"],
    }


@pytest.mark.asyncio
async def test_plain_provision_sends_no_network_policy(tmp_path: Path) -> None:
    client = FakeClient()
    provider = governed_provider(client, tmp_path)
    await provider.provision(run())
    assert client.created is not None
    assert client.created["network"] is None


@pytest.mark.asyncio
async def test_keep_alive_renews_the_platform_ttl_and_throttles(tmp_path: Path) -> None:
    """A Run may outlive the sandbox TTL, so the TTL is extended while it works."""

    now = [1000.0]
    client = FakeClient()
    provider = E2BSandboxProvider(
        client=client,
        local_root=tmp_path,
        template="harness-template",
        timeout_seconds=600,
        allow_internet_access=True,
        remote_workspace_root="/home/user/harness",
        clock=lambda: now[0],
    )
    handle = await provider.provision(run())

    await provider.execute(handle, ("bash", "-lc", "true"))
    assert client.sandbox.renewals == [600]

    # Inside the interval (half the TTL) another tool call must not renew again.
    now[0] += 100
    await provider.execute(handle, ("bash", "-lc", "true"))
    assert client.sandbox.renewals == [600]

    now[0] += 400
    await provider.execute(handle, ("bash", "-lc", "true"))
    assert client.sandbox.renewals == [600, 600]

    await provider.destroy(handle)


@pytest.mark.asyncio
async def test_keep_alive_failure_does_not_fail_the_command(tmp_path: Path) -> None:
    """A transient renewal error must not take down an otherwise working Run."""

    client = FakeClient()
    provider = governed_provider(client, tmp_path)

    async def failing_renew(timeout_seconds: int) -> None:
        del timeout_seconds
        raise RuntimeError("platform unavailable")

    handle = await provider.provision(run())
    client.sandbox.renew = failing_renew  # type: ignore[method-assign]
    result = await provider.execute(handle, ("bash", "-lc", "true"))
    assert result.exit_code == 0
    await provider.destroy(handle)


def warm_metadata(
    *,
    tenant: str = "tenant-a",
    session: str = "session-a",
    run_id: str = "run-earlier",
    egress: str | None = None,
) -> dict[str, str]:
    metadata = {
        "harness.tenant": tenant,
        "harness.session": session,
        "harness.run": run_id,
        "harness.keep": "keep_warm",
    }
    if egress is not None:
        metadata["harness.egress"] = egress
    return metadata


def warm_provider(client: FakeClient, tmp_path: Path, **overrides: Any) -> E2BSandboxProvider:
    parameters: dict[str, Any] = {
        "client": client,
        "local_root": tmp_path,
        "template": "harness-template",
        "timeout_seconds": 900,
        "allow_internet_access": True,
        "remote_workspace_root": "/home/user/harness",
        "idle_policy": "keep_warm",
    }
    parameters.update(overrides)
    return E2BSandboxProvider(**parameters)


@pytest.mark.asyncio
async def test_reuse_takes_the_session_warm_sandbox_instead_of_creating(tmp_path: Path) -> None:
    client = FakeClient()
    client.managed = [("sbx-warm", warm_metadata())]
    provider = warm_provider(client, tmp_path)

    handle = await provider.provision(run())

    assert client.attached == ["sbx-warm"]
    assert handle.sandbox_id == client.warm_sandbox.id
    assert client.create_calls == 0
    assert handle.remote_workspace == "/home/user/harness/run-a"


@pytest.mark.asyncio
async def test_reuse_is_scoped_to_the_session_and_tenant(tmp_path: Path) -> None:
    client = FakeClient()
    client.managed = [
        ("sbx-other-session", warm_metadata(session="session-b")),
        ("sbx-other-tenant", warm_metadata(tenant="tenant-b")),
        ("sbx-cold", {"harness.tenant": "tenant-a", "harness.session": "session-a"}),
    ]
    provider = warm_provider(client, tmp_path)
    handle = await provider.provision(run())
    assert handle.sandbox_id == client.sandbox.id
    assert client.create_calls == 1
    assert client.attached == []
    assert client.killed == []


@pytest.mark.asyncio
async def test_reuse_requires_an_identical_egress_policy(tmp_path: Path) -> None:
    """A warm sandbox must never widen the policy a Run declared."""

    client = FakeClient()
    client.managed = [
        ("sbx-restricted", warm_metadata(egress="deadbeefdeadbeef")),
    ]
    provider = warm_provider(client, tmp_path)
    await provider.provision_with_egress(run(), SandboxEgress(deny_internet=True))
    assert client.create_calls == 1
    assert client.attached == []


@pytest.mark.asyncio
async def test_reuse_matches_when_the_policy_fingerprint_agrees(tmp_path: Path) -> None:
    egress = SandboxEgress(allow_hosts=("mcp.internal.example",))
    fingerprint = E2BSandboxProvider.egress_fingerprint(egress)
    assert fingerprint is not None

    client = FakeClient()
    client.managed = [("sbx-warm", warm_metadata(egress=fingerprint))]
    provider = warm_provider(client, tmp_path)
    handle = await provider.provision_with_egress(run(), egress)
    assert client.attached == ["sbx-warm"]
    assert handle.sandbox_id == client.warm_sandbox.id
    assert client.create_calls == 0


@pytest.mark.asyncio
async def test_unhealthy_warm_sandbox_is_discarded_and_replaced(tmp_path: Path) -> None:
    client = FakeClient()
    client.managed = [("sbx-warm", warm_metadata())]
    client.warm_sandbox.ping_ok = False
    provider = warm_provider(client, tmp_path)

    handle = await provider.provision(run())

    assert client.killed == ["sbx-warm"]
    assert client.create_calls == 1
    assert handle.sandbox_id == client.sandbox.id


@pytest.mark.asyncio
async def test_destroy_keeps_the_sandbox_warm_and_drops_only_the_workspace(
    tmp_path: Path,
) -> None:
    client = FakeClient()
    client.managed = [("sbx-warm", warm_metadata())]
    provider = warm_provider(client, tmp_path)
    handle = await provider.provision(run())

    await provider.destroy(handle)

    assert client.warm_sandbox.removed == ["/home/user/harness/run-a"]
    assert client.sandbox.killed is False
    assert not handle.path.exists()


@pytest.mark.asyncio
async def test_destroy_retires_a_sandbox_whose_workspace_cannot_be_cleaned(
    tmp_path: Path,
) -> None:
    """Reusing contaminated state is worse than losing the warm sandbox."""

    client = FakeClient()
    client.managed = [("sbx-warm", warm_metadata())]
    provider = warm_provider(client, tmp_path)
    handle = await provider.provision(run())
    client.warm_sandbox.remove_failure = RuntimeError("execd unavailable")

    await provider.destroy(handle)

    assert client.warm_sandbox.killed is True


@pytest.mark.asyncio
async def test_reaper_leaves_warm_sandboxes_alone(tmp_path: Path) -> None:
    """Warm sandboxes outlive their Run; the platform TTL reclaims them."""

    client = FakeClient()
    client.managed = [
        ("sbx-warm", warm_metadata()),
        ("sbx-orphan", {"harness.tenant": "tenant-a", "harness.run": "run-done"}),
    ]
    provider = warm_provider(client, tmp_path)

    async def is_active(tenant_id: str, run_id: str) -> bool:
        del tenant_id, run_id
        return False

    provider.bind_run_liveness(is_active)
    assert await provider.reap_expired() == 1
    assert client.killed == ["sbx-orphan"]


@pytest.mark.asyncio
async def test_new_sandbox_records_the_warm_marker_and_policy(tmp_path: Path) -> None:
    client = FakeClient()
    provider = warm_provider(client, tmp_path)
    egress = SandboxEgress(allow_hosts=("mcp.internal.example",))
    await provider.provision_with_egress(run(), egress)

    assert client.created is not None
    metadata = client.created["metadata"]
    assert metadata["harness.keep"] == "keep_warm"
    assert metadata["harness.egress"] == E2BSandboxProvider.egress_fingerprint(egress)


@pytest.mark.asyncio
async def test_validate_template_accepts_a_ready_template(tmp_path: Path) -> None:
    client = FakeClient()
    provider = governed_provider(client, tmp_path)
    assert await provider.validate_template() == "READY"
    assert client.template_queries == ["harness-template"]


@pytest.mark.asyncio
async def test_validate_template_refuses_a_missing_or_failed_template(tmp_path: Path) -> None:
    """An alias or ID that resolves to nothing must not reach the first Run."""

    client = FakeClient()
    provider = governed_provider(client, tmp_path)

    client.template_status = "MISSING"
    with pytest.raises(ValueError, match="not in the platform catalogue"):
        await provider.validate_template()

    client.template_status = "FAILED"
    with pytest.raises(ValueError, match="is FAILED, not READY"):
        await provider.validate_template()


@pytest.mark.asyncio
async def test_validate_template_skips_when_the_platform_cannot_be_asked(
    tmp_path: Path,
) -> None:
    client = FakeClient()
    client.template_status = None
    provider = governed_provider(client, tmp_path)
    assert await provider.validate_template() is None


@pytest.mark.asyncio
async def test_create_snapshot_returns_a_reusable_reference(tmp_path: Path) -> None:
    client = FakeClient()
    provider = governed_provider(client, tmp_path)
    handle = await provider.provision(run())
    reference = await provider.create_snapshot(handle, name="golden-v1")
    assert reference == "snap-new"
    assert client.sandbox.snapshot_names == ["golden-v1"]


@pytest.mark.asyncio
async def test_volume_mounts_reach_the_platform(tmp_path: Path) -> None:
    client = FakeClient()
    provider = warm_provider(client, tmp_path, volume_mounts={"/data": "team-data"})
    await provider.provision(run())
    assert client.created is not None
    assert client.created["volume_mounts"] == {"/data": "team-data"}


@pytest.mark.asyncio
async def test_provision_without_volumes_sends_none(tmp_path: Path) -> None:
    client = FakeClient()
    provider = governed_provider(client, tmp_path)
    await provider.provision(run())
    assert client.created is not None
    assert client.created["volume_mounts"] is None


def paused_provider(client: FakeClient, tmp_path: Path, **overrides: Any) -> E2BSandboxProvider:
    return warm_provider(client, tmp_path, idle_policy="pause", **overrides)


@pytest.mark.asyncio
async def test_pause_policy_snapshots_idle_state_and_cleans_the_workspace(
    tmp_path: Path,
) -> None:
    """Pausing keeps the live state and releases the host; only the workspace goes."""

    client = FakeClient()
    client.managed = [("sbx-paused", warm_metadata())]
    provider = paused_provider(client, tmp_path)
    handle = await provider.provision(run())

    await provider.destroy(handle)

    assert client.warm_sandbox.removed == ["/home/user/harness/run-a"]
    assert client.warm_sandbox.paused is True
    assert client.warm_sandbox.killed is False


@pytest.mark.asyncio
async def test_pause_policy_retires_instead_of_pausing_a_dirty_workspace(
    tmp_path: Path,
) -> None:
    client = FakeClient()
    client.managed = [("sbx-paused", warm_metadata())]
    provider = paused_provider(client, tmp_path)
    handle = await provider.provision(run())
    client.warm_sandbox.remove_failure = RuntimeError("execd unavailable")

    await provider.destroy(handle)

    assert client.warm_sandbox.killed is True
    assert client.warm_sandbox.paused is False


@pytest.mark.asyncio
async def test_pause_failure_leaves_a_warm_sandbox_without_failing_teardown(
    tmp_path: Path,
) -> None:
    client = FakeClient()
    client.managed = [("sbx-paused", warm_metadata())]
    provider = paused_provider(client, tmp_path)
    handle = await provider.provision(run())
    client.warm_sandbox.pause_failure = RuntimeError("platform refused pause")

    await provider.destroy(handle)

    assert client.warm_sandbox.killed is False


@pytest.mark.asyncio
async def test_a_paused_sandbox_is_adopted_and_resumed_by_the_next_run(
    tmp_path: Path,
) -> None:
    """A paused instance is the session's continuity carrier."""

    client = FakeClient()
    client.managed = [("sbx-paused", {"harness.tenant": "tenant-a",
                                      "harness.session": "session-a",
                                      "harness.run": "run-earlier",
                                      "harness.keep": "pause"})]
    provider = paused_provider(client, tmp_path)
    handle = await provider.provision(run())
    assert client.attached == ["sbx-paused"]
    assert handle.sandbox_id == client.warm_sandbox.id
    assert client.create_calls == 0


@pytest.mark.asyncio
async def test_reaper_leaves_paused_sandboxes_alone(tmp_path: Path) -> None:
    client = FakeClient()
    client.managed = [
        ("sbx-paused", {"harness.tenant": "tenant-a", "harness.run": "run-done",
                        "harness.keep": "pause"}),
        ("sbx-orphan", {"harness.tenant": "tenant-a", "harness.run": "run-done"}),
    ]
    provider = paused_provider(client, tmp_path)

    async def is_active(tenant_id: str, run_id: str) -> bool:
        del tenant_id, run_id
        return False

    provider.bind_run_liveness(is_active)
    assert await provider.reap_expired() == 1
    assert client.killed == ["sbx-orphan"]


@pytest.mark.asyncio
async def test_destroy_policy_still_deletes_the_sandbox(tmp_path: Path) -> None:
    client = FakeClient()
    provider = governed_provider(client, tmp_path)
    handle = await provider.provision(run())
    await provider.destroy(handle)
    assert client.sandbox.killed is True


def test_unknown_idle_policy_is_refused(tmp_path: Path) -> None:
    client = FakeClient()
    with pytest.raises(ValueError, match="unknown idle policy"):
        E2BSandboxProvider(
            client=client,
            local_root=tmp_path,
            template="harness-template",
            timeout_seconds=900,
            allow_internet_access=True,
            remote_workspace_root="/home/user/harness",
            idle_policy="suspend",
        )


@pytest.mark.asyncio
async def test_idle_policy_is_recorded_in_the_sandbox_metadata(tmp_path: Path) -> None:
    client = FakeClient()
    provider = paused_provider(client, tmp_path)
    await provider.provision(run())
    assert client.created is not None
    assert client.created["metadata"]["harness.keep"] == "pause"


def test_log_payload_parsing_accepts_both_platform_shapes() -> None:
    """CubeSandbox says `line`, the v2 shape says `message`; both must read."""

    cube = {"logs": [{"timestamp": "t1", "line": "shim pid 1"},
                     {"timestamp": "t2", "line": "load spec finish"}]}
    v2 = {"logs": [{"timestamp": "t1", "message": "create req start"}]}

    assert _parse_log_payload(cube, 40) == ("t1 shim pid 1", "t2 load spec finish")
    assert _parse_log_payload(v2, 40) == ("t1 create req start",)


def test_log_payload_parsing_is_bounded_and_defensive() -> None:
    entries = {"logs": [{"timestamp": f"t{i}", "line": f"line {i}"} for i in range(10)]}
    assert _parse_log_payload(entries, 3) == ("t7 line 7", "t8 line 8", "t9 line 9")
    # An unexpected shape yields nothing rather than raising into the Run.
    assert _parse_log_payload({"logs": "not-a-list"}, 40) == ()
    assert _parse_log_payload(None, 40) == ()
    assert _parse_log_payload({"logs": [{"timestamp": "t"}]}, 40) == ()


@pytest.mark.asyncio
async def test_sandbox_logs_and_metrics_pass_through_the_provider(tmp_path: Path) -> None:
    client = FakeClient()
    client.log_lines = ("t1 boot", "t2 execd ready")
    client.metric_sample = SandboxResourceUsage(
        cpu_used_pct=12.5,
        mem_used_bytes=1024,
        mem_total_bytes=2048,
        disk_used_bytes=10,
        disk_total_bytes=100,
        sampled_at=datetime(2026, 9, 18, tzinfo=UTC),
    )
    provider = governed_provider(client, tmp_path)
    handle = await provider.provision(run())

    assert await provider.sandbox_logs(handle, 1) == ("t2 execd ready",)
    assert client.log_queries == [(handle.sandbox_id, 1)]

    sample = await provider.sandbox_metrics(handle)
    assert sample is not None and sample.cpu_used_pct == 12.5


@pytest.mark.asyncio
async def test_metrics_absent_is_reported_as_nothing(tmp_path: Path) -> None:
    """A platform without per-sandbox metrics must not look like an idle sandbox."""

    client = FakeClient()
    client.metric_sample = None
    provider = governed_provider(client, tmp_path)
    handle = await provider.provision(run())
    assert await provider.sandbox_metrics(handle) is None


@pytest.mark.asyncio
async def test_provision_refuses_a_data_plane_older_than_the_sdk_needs(
    tmp_path: Path,
) -> None:
    """0.3.0 predates closing stdin, which every tool execution depends on."""

    client = FakeClient()
    client.envd = "0.3.0"
    provider = governed_provider(client, tmp_path)
    with pytest.raises(ValueError, match="older than"):
        await provider.provision(run())


@pytest.mark.asyncio
async def test_environment_is_checked_once_per_provider(tmp_path: Path) -> None:
    client = FakeClient()
    provider = governed_provider(client, tmp_path)
    first = await provider.provision(run())
    await provider.destroy(first)
    second = await provider.provision(run().model_copy(update={"run_id": "run-b"}))
    await provider.destroy(second)

    assert client.envd_queries == [first.sandbox_id]
    assert await provider.validate_environment(second.sandbox_id) == "0.5.11"


@pytest.mark.asyncio
async def test_unreadable_data_plane_version_does_not_block_provisioning(
    tmp_path: Path,
) -> None:
    """A version the platform will not report is unknown, not incompatible."""

    client = FakeClient()
    client.envd = None
    provider = governed_provider(client, tmp_path)
    handle = await provider.provision(run())
    assert handle.sandbox_id == client.sandbox.id


@pytest.mark.asyncio
async def test_list_managed_keeps_the_entries_it_could_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CubeSandbox omits ``endAt`` while a sandbox starts, and the SDK parser raises.

    That payload feeds the capacity gauge, warm reuse and the reaper, so an
    unreadable page must end the pass rather than fail governance entirely.
    """

    class Item:
        sandbox_id = "sbx-1"
        metadata = {"harness.tenant": "tenant-a", "harness.run": "run-1"}

    class Paginator:
        def __init__(self) -> None:
            self.calls = 0
            self.has_next = True

        async def next_items(self, *, api_key: str) -> list[Item]:
            del api_key
            self.calls += 1
            if self.calls == 1:
                return [Item()]
            raise KeyError("endAt")

    monkeypatch.setattr("harness.sandbox.e2b.AsyncSandbox.list", lambda **_: Paginator())
    client = SdkE2BClient(api_key="key")

    assert await client.list_managed() == [
        ("sbx-1", {"harness.tenant": "tenant-a", "harness.run": "run-1"})
    ]


@pytest.mark.asyncio
async def test_list_managed_returns_nothing_when_the_first_page_is_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Paginator:
        has_next = True

        async def next_items(self, *, api_key: str) -> list[object]:
            del api_key
            raise KeyError("endAt")

    monkeypatch.setattr("harness.sandbox.e2b.AsyncSandbox.list", lambda **_: Paginator())
    client = SdkE2BClient(api_key="key")

    assert await client.list_managed() == []


class _RecordingFilesystem:
    """Records the deadline each collection RPC is issued with."""

    def __init__(self) -> None:
        self.list_timeouts: list[float | None] = []
        self.read_timeouts: list[float | None] = []

    async def list(
        self,
        path: str,
        depth: int = 1,
        user: str | None = None,
        request_timeout: float | None = None,
    ) -> list[object]:
        del path, depth, user
        self.list_timeouts.append(request_timeout)
        return []

    async def read(
        self,
        path: str,
        format: str = "text",
        user: str | None = None,
        request_timeout: float | None = None,
        gzip: bool = False,
    ) -> bytearray:
        del path, format, user, gzip
        self.read_timeouts.append(request_timeout)
        return bytearray(b"collected")


class _FilesystemOnlySandbox:
    def __init__(self) -> None:
        self.sandbox_id = "e2b-sandbox-files"
        self.files = _RecordingFilesystem()


@pytest.mark.asyncio
async def test_workspace_collection_sets_its_own_request_deadline() -> None:
    """Collection must not inherit the connection's `request_timeout`.

    That value is sized for control-plane calls (CubeSandbox pins 30s) and the
    e2b SDK turns it into the deadline of the *whole* call. Enumerating a
    workspace the model just unpacked legitimately outlives it — and because
    collection runs after the answer is already durable, the platform would
    otherwise fail a Run that had already succeeded.
    """

    sandbox = _FilesystemOnlySandbox()
    remote = SdkE2BRemoteSandbox(cast(Any, sandbox))

    assert await remote.list_files("/workspace") == []
    assert await remote.download("/workspace/report.md") == b"collected"

    # The connection's control-plane timeout is 30s; collection must outlive it.
    assert sandbox.files.list_timeouts == sandbox.files.read_timeouts
    assert sandbox.files.list_timeouts
    assert all(timeout is not None and timeout > 30 for timeout in sandbox.files.list_timeouts)


class _ExitedProcess:
    async def wait(self) -> object:
        return SimpleNamespace(exit_code=0)


class _RecordingCommands:
    def __init__(self) -> None:
        self.runs: list[dict[str, object]] = []

    async def run(self, command: str, **kwargs: object) -> _ExitedProcess:
        self.runs.append({"command": command, **kwargs})
        return _ExitedProcess()


class _CommandsOnlySandbox:
    def __init__(self) -> None:
        self.sandbox_id = "e2b-sandbox-commands"
        self.commands = _RecordingCommands()


@pytest.mark.asyncio
async def test_remote_session_sets_its_own_request_deadline() -> None:
    """Opening a command stream must not inherit the control-plane deadline.

    The e2b SDK turns the connection's `request_timeout` into the deadline for
    opening the envd stream, and CubeSandbox pins that value at 30s for
    control-plane work. A stream slower to open than that has the data-plane
    proxy answer 504, which the SDK raises as a TimeoutException carrying the
    proxy's HTML body — so a transient hiccup fails a Run the model had nothing
    to do with. The command's own budget must stay with the caller.
    """

    sandbox = _CommandsOnlySandbox()
    session = SdkE2BRemoteSession(cast(Any, sandbox))

    await session.start(["bash", "-lc", "echo hi"], "/workspace", {})

    assert sandbox.commands.runs
    run = sandbox.commands.runs[0]
    request_timeout = run["request_timeout"]
    assert request_timeout is not None and request_timeout > 30
    # `timeout=0` keeps the local deadline authoritative for command duration.
    assert run["timeout"] == 0

    await session.wait()
