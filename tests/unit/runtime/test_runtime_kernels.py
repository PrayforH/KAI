"""Deployment kernel selection must preserve routing and sandbox boundaries."""

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

import pytest

from harness.config import Settings
from harness.core.errors import ConflictError
from harness.core.manifest import load_manifest
from harness.core.ports import AgentRegistry
from harness.runtime.base import RuntimeContext
from harness.runtime.fake import FakeRuntime
from harness.runtime.registry_codex_runtime import RegistryRuntimeRouter

ROOT = Path(__file__).parents[3]


def test_runtime_kernels_parse_validated_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_RUNTIME_KERNELS", '["claude-agent-sdk","deepagents"]')
    assert Settings().runtime_kernels == {"claude-agent-sdk", "deepagents"}
    for value in ("[]", '["unknown"]', "invalid"):
        monkeypatch.setenv("HARNESS_RUNTIME_KERNELS", value)
        with pytest.raises(ValueError):
            Settings()


def test_router_requires_exact_explicit_kernel_wiring() -> None:
    registry = cast(AgentRegistry, AsyncMock())
    for kernels in (None, {"deepagents"}, set()):
        kwargs = {} if kernels is None else {"enabled_runtimes": kernels}
        with pytest.raises(ValueError):
            RegistryRuntimeRouter(
                registry=registry, runtimes={"claude-agent-sdk": FakeRuntime()}, **kwargs
            )


@pytest.mark.asyncio
async def test_disabled_kernel_is_refused_without_fallback() -> None:
    snapshot = load_manifest("agents/helper-agent/agent.yaml")
    snapshot = snapshot.model_copy(
        update={
            "manifest": snapshot.manifest.model_copy(
                update={
                    "spec": snapshot.manifest.spec.model_copy(
                        update={"runtime": "codex-app-server"}
                    )
                }
            )
        }
    )
    registry = AsyncMock()
    registry.get.return_value = SimpleNamespace(snapshot=snapshot.model_dump(mode="json"))
    router = RegistryRuntimeRouter(
        registry=registry,
        runtimes={"claude-agent-sdk": FakeRuntime(), "deepagents": FakeRuntime()},
        enabled_runtimes={"claude-agent-sdk", "deepagents"},
    )
    session = SimpleNamespace(
        tenant_id="tenant",
        resolved_agent_owner_user_id="user",
        agent_name="helper-agent",
        agent_version="1.0.0",
        runtime_type="codex-app-server",
    )
    with pytest.raises(ConflictError, match="not enabled.*codex-app-server"):
        _ = [
            event
            async for event in router.execute(
                cast(RuntimeContext, SimpleNamespace(session=session))
            )
        ]


@pytest.mark.parametrize(
    ("kernels", "bwrap_status", "expected_status", "worker_started"),
    [
        (None, 23, 23, False),
        ('["claude-agent-sdk","deepagents"]', 23, 0, True),
        ('["codex-app-server"]', 23, 23, False),
        ('["codex-app-server"]', 0, 0, True),
        ("[]", 0, 1, False),
        ("invalid", 0, 1, False),
    ],
)
def test_worker_sandbox_check_tracks_enabled_kernels(
    tmp_path: Path,
    kernels: str | None,
    bwrap_status: int,
    expected_status: int,
    worker_started: bool,
) -> None:
    # Replace executables only; run the real entrypoint and Settings parser.
    bwrap = tmp_path / "bwrap"
    bwrap.write_text(f"#!/bin/sh\nexit {bwrap_status}\n")
    bwrap.chmod(0o755)
    script = (
        (ROOT / "deploy/docker/entrypoint-worker.sh")
        .read_text()
        .replace("/opt/codex/vendor/x86_64-unknown-linux-musl/codex-resources/bwrap", str(bwrap))
    )
    worker = tmp_path / "harness-worker"
    worker.write_text("#!/bin/sh\necho worker-started\n")
    worker.chmod(0o755)
    env = {
        **os.environ,
        "HARNESS_RUNTIME": "multi",
        "HARNESS_SANDBOX_PROVIDER": "local",
        "PATH": f"{tmp_path}:{Path(sys.executable).parent}:{os.environ['PATH']}",
        "PYTHONPATH": str(ROOT / "src"),
    }
    env.pop("HARNESS_RUNTIME_KERNELS", None)
    if kernels is not None:
        env["HARNESS_RUNTIME_KERNELS"] = kernels
    result = subprocess.run(["/bin/sh"], input=script, env=env, text=True, capture_output=True)
    assert result.returncode == expected_status, result.stderr
    assert ("worker-started" in result.stdout) == worker_started
