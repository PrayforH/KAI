"""Measure CubeSandbox workspace collection against the configured deployment.

Creates one temporary sandbox, fills its workspace with the same order of
magnitude of entries a Run produces after unpacking an 18MB archive, then times
the collection the platform performs. Also reports whether the template can
archive the workspace in one step, which is what the Daytona and Kubernetes
providers already do. Deletes the sandbox.

Every stage is isolated: a stage that dies still leaves a report, because the
failure itself is the measurement.

Run inside a container that has the platform settings, e.g.
``docker exec agent-evolution-173-api python /tmp/probe_cubesandbox_collect.py``.
"""

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from harness.config import Settings
from harness.core.models import Run, RunStatus
from harness.sandbox.cubesandbox import build_cubesandbox_provider


def _describe(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"[:600]


async def _stage(
    report: dict[str, Any],
    name: str,
    work: Any,
) -> Any:
    started = time.monotonic()
    try:
        value = await work()
    except BaseException as error:  # noqa: BLE001 - the failure itself is the measurement
        report[name] = {"error": _describe(error), "seconds": round(time.monotonic() - started, 2)}
        return None
    report[name] = {"ok": True, "seconds": round(time.monotonic() - started, 2)}
    return value


async def probe(*, entries: int) -> None:
    settings = Settings()
    provider = build_cubesandbox_provider(settings)
    now = datetime.now(UTC)
    identifier = f"cube-collect-probe-{uuid4().hex}"
    run = Run(
        run_id=identifier,
        session_id=identifier,
        tenant_id="sandbox-integration",
        status=RunStatus.PROVISIONING,
        idempotency_key=identifier,
        created_at=now,
        updated_at=now,
    )
    report: dict[str, Any] = {"entries": entries}
    handle = await provider.provision(run)
    report["sandbox_id"] = handle.sandbox_id
    try:
        await _stage(
            report,
            "prepare",
            lambda: provider.prepare(
                handle.model_copy(update={"deferred_tool_execution": True})
            ),
        )

        tools = await _stage(
            report,
            "tools",
            lambda: provider.execute(
                handle,
                [
                    "bash",
                    "-lc",
                    "for t in tar find du; do printf '%s=' $t; command -v $t || echo MISSING; done",
                ],
                timeout_seconds=60,
            ),
        )
        if tools is not None:
            report["tools"]["stdout"] = tools.stdout.strip()

        filled = await _stage(
            report,
            "fill",
            lambda: provider.execute(
                handle,
                [
                    "bash",
                    "-lc",
                    f"mkdir -p deep; i=0; while [ $i -lt {entries} ]; do "
                    'd=$((i % 50)); mkdir -p deep/d$d; printf "x" > deep/d$d/f$i.txt; '
                    "i=$((i+1)); done; find . -type f | wc -l",
                ],
                timeout_seconds=900,
            ),
        )
        if filled is not None:
            report["fill"]["files"] = filled.stdout.strip()

        archived = await _stage(
            report,
            "archive",
            lambda: provider.execute(
                handle,
                ["bash", "-lc", "tar czf /tmp/ws.tar.gz -C . . && ls -l /tmp/ws.tar.gz"],
                timeout_seconds=900,
            ),
        )
        if archived is not None:
            report["archive"]["output"] = (archived.stdout + archived.stderr).strip()[-300:]

        await _stage(report, "collect", lambda: provider.collect(handle))
        report["collected_files"] = sum(1 for path in handle.path.rglob("*") if path.is_file())

        # The candidate replacement: one archive inside the sandbox, one streamed
        # read back. Reaches into the provider only because this is a probe.
        remote = getattr(provider, "_sandboxes", {}).get(handle.sandbox_id)
        sdk = getattr(remote, "_sandbox", None)
        workspace = handle.remote_workspace
        if sdk is not None and workspace is not None:

            async def stream_archive() -> None:
                archive_path = "/tmp/harness-collect-probe.tar.gz"
                await sdk.commands.run(
                    f"tar czf {archive_path} -C {workspace} .",
                    timeout=0,
                )
                stream = await sdk.files.read(archive_path, format="stream")
                total = 0
                try:
                    async for chunk in stream:
                        total += len(chunk)
                finally:
                    await stream.aclose()
                report["stream_archive"]["bytes"] = total

            await _stage(report, "stream_archive", stream_archive)
    finally:
        try:
            await provider.destroy(handle)
        except BaseException as error:  # noqa: BLE001 - cleanup must not hide the report
            report["destroy"] = _describe(error)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entries", type=int, default=4500, help="Workspace file count to create")
    args = parser.parse_args()
    asyncio.run(probe(entries=args.entries))
