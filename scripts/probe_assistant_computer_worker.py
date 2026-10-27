"""Isolated Worker orchestration with deterministic runtime and real Computer.

No model credentials or production identity are used. Probe mode uses fixture
Run/events in memory; canonical mode persists fixture accounts, bindings and
execution records in the isolated database and exercises node authorization.
Neither mode is real assistant/model acceptance.
"""

import argparse
import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import httpx
from sqlalchemy import Table
from sqlalchemy.engine import make_url

from harness.adapters.memory import (
    InMemoryArtifactRepository,
    InMemoryArtifactStore,
    InMemoryEventBus,
    InMemoryEventRepository,
    InMemoryRunRepository,
    InMemorySessionRepository,
)
from harness.application.artifacts import ArtifactService
from harness.application.events import EventService
from harness.assistant.constants import PERSONAL_ASSISTANT_THREAD
from harness.computer.control import HttpComputerControl
from harness.computer.docker import DockerComputerDriver
from harness.computer.registry import ComputerRegistry
from harness.computer.sandbox import ComputerSandboxProvider
from harness.computer.storage import AssistantComputerRow, ComputerLeaseRow
from harness.core.errors import NotFoundError
from harness.core.models import AguiThreadBinding, Run, RunStatus, Session
from harness.core.ports import EventRepository, RunRepository, SessionRepository
from harness.runtime.base import RuntimeContext, RuntimeEvent
from harness.runtime.fake import FakeRuntime
from harness.sandbox.lease import SandboxLeaseService
from harness.storage.database import create_database, create_schema
from harness.storage.models import AguiThreadBindingRow, TenantMembershipRow, UserRow
from harness.storage.platform_repositories import PostgresSessionRepository
from harness.storage.repositories import PostgresEventRepository, PostgresRunRepository
from harness.storage.sandbox_lease_repository import PostgresSandboxLeaseRepository
from harness.worker.orchestrator import RunOrchestrator


class ToolsRuntime(FakeRuntime):
    def __init__(self, report: dict[str, Any]) -> None:
        super().__init__()
        self.report = report

    async def execute(self, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        command, files = context.sandbox_command_executor, context.sandbox_file_plane
        assert command is not None and files is not None
        if context.run.input["round"] == 1:
            content = b"KAI-CHUNK79-" * 300_000
            await files.upload_files([("inputs/chunk.bin", content), ("inputs/empty", b"")])
            assert await files.download_file("inputs/chunk.bin", max_bytes=len(content)) == content
            assert await files.download_file("inputs/empty", max_bytes=0) == b""
            self.report["chunkedAndEmptyTransferConfirmed"] = True
            source = (
                "from pathlib import Path; import os,json; "
                "assert Path('inputs/staged.txt').read_text()=='current-run-input'; "
                "Path('outputs').mkdir(exist_ok=True); "
                "Path('outputs/first.txt').write_text('first'); "
                "Path('project.txt').write_text('retained-project'); "
                "p=Path.home()/'bin'/'kai-worker-fixture'; "
                "p.write_text('#!/bin/sh\\nprintf retained-tool'); p.chmod(0o700); "
                "print(json.dumps({'uids':os.getresuid()}))"
            )
            result = await command(["python3", "-c", source], None, 30)
            assert result.exit_code == 0
            self.report["taskUids"] = json.loads(result.stdout)["uids"]
            assert self.report["taskUids"] == [28000, 28000, 28000]
        else:
            source = (
                "from pathlib import Path; import subprocess; "
                "assert Path('project.txt').read_text()=='retained-project'; "
                "assert Path('outputs/first.txt').read_text()=='first'; "
                "tool=Path.home()/'bin'/'kai-worker-fixture'; "
                "assert subprocess.check_output([str(tool)]).decode()=='retained-tool'; "
                "Path('outputs/second.txt').write_text('second')"
            )
            result = await command(["python3", "-c", source], None, 30)
            assert result.exit_code == 0
            self.report["previousFilesAndExecutableRetained"] = True
        async for event in super().execute(context):
            yield event


async def probe(config_path: Path, output: Path) -> None:
    config = json.loads(config_path.read_text())
    if make_url(config["database_url"]).database != "harness_computer_probe":
        raise ValueError("Worker probe requires the isolated harness_computer_probe database")
    canonical = config.get("authorization", "probe") == "canonical"
    engine, sessions = create_database(config["database_url"])
    if canonical:
        tenant, user = config["tenant_id"], config["user_id"]
        if not tenant.startswith("computer-access-probe-") or not user.startswith("fixture-owner"):
            raise ValueError("canonical probe requires generated fixture identity")
        await create_schema(engine)
    else:
        async with engine.begin() as connection:
            await connection.run_sync(
                lambda sync: AssistantComputerRow.metadata.create_all(
                    sync,
                    tables=[
                        cast(Table, AssistantComputerRow.__table__),
                        cast(Table, ComputerLeaseRow.__table__),
                    ],
                )
            )
        tenant, user = "computer-worker-probe-" + uuid4().hex, "fixture-owner"
    registry = ComputerRegistry(sessions, lease_ttl_seconds=60)

    def clock() -> datetime:
        return datetime.now(UTC)

    session = Session(
        session_id="fixture-session",
        tenant_id=tenant,
        user_id=user,
        agent_name="fixture-assistant",
        agent_version="1",
        created_at=clock(),
    )
    report: dict[str, Any] = {
        "tenant": tenant,
        "checkedAt": clock().isoformat(),
        "scope": (
            "Real Worker orchestration and Docker Computer; deterministic runtime and "
            "in-memory Run/events; no real model or production chats"
        ),
    }
    async with httpx.AsyncClient(
        base_url=config["control_url"],
        trust_env=False,
        headers={"Authorization": "Bearer " + config["secret"]},
    ) as client:
        driver = DockerComputerDriver(HttpComputerControl(client), image=config["image"])
        run_repository: RunRepository
        session_repository: SessionRepository
        events_repository: EventRepository
        sandbox_leases: SandboxLeaseService | None = None
        if canonical:
            run_repository = PostgresRunRepository(sessions, fence_computers=True)
            session_repository = PostgresSessionRepository(sessions)
            events_repository = PostgresEventRepository(sessions)
            sandbox_leases = SandboxLeaseService(PostgresSandboxLeaseRepository(sessions))
            binding = AguiThreadBinding(
                tenant_id=tenant,
                user_id=user,
                thread_id=PERSONAL_ASSISTANT_THREAD,
                session_id=session.session_id,
                created_at=clock(),
                updated_at=clock(),
            )
            async with sessions.begin() as db:
                db.add(
                    UserRow(
                        user_id=user,
                        email=tenant + "@fixture.invalid",
                        display_name="fixture",
                        created_at=clock(),
                        updated_at=clock(),
                    )
                )
                db.add(
                    TenantMembershipRow(
                        tenant_id=tenant, user_id=user, role="owner", created_at=clock()
                    )
                )
                db.add(
                    AguiThreadBindingRow(
                        tenant_id=tenant,
                        user_id=user,
                        thread_id=binding.thread_id,
                        session_id=session.session_id,
                        payload=binding.model_dump(mode="json"),
                    )
                )
            report["scope"] = (
                "Real Worker, account/thread/Run/lease records in isolated PostgreSQL "
                "and canonical node authorization; deterministic runtime; "
                "no real model or production chats"
            )
        else:
            run_repository, session_repository = (
                InMemoryRunRepository(),
                InMemorySessionRepository(),
            )
            events_repository = InMemoryEventRepository()
        await session_repository.add(session)
        report["authorization"] = "canonical" if canonical else "probe"
        artifacts = ArtifactService(
            runs=run_repository,
            repository=InMemoryArtifactRepository(),
            store=InMemoryArtifactStore(),
            id_generator=lambda prefix: prefix + uuid4().hex,
        )

        async def stage(
            tenant: str, owner: str, name: str, version: str, workspace: Path, graph: bool
        ) -> tuple[str, ...]:
            (workspace / "inputs").mkdir(exist_ok=True)
            (workspace / "inputs/staged.txt").write_text("current-run-input")
            return ()

        async def execute(index: int, runtime: FakeRuntime) -> Run:
            run = Run(
                run_id="fixture-run-" + str(index),
                session_id=session.session_id,
                tenant_id=tenant,
                status=RunStatus.QUEUED,
                idempotency_key=str(index),
                created_at=clock(),
                updated_at=clock(),
                input={"prompt": "fixture", "round": index},
            )
            await run_repository.add(run)
            sandbox = ComputerSandboxProvider(
                registry, driver, session, run_reader=run_repository.get, max_bytes=32 * 1024**2
            )
            worker = RunOrchestrator(
                sessions=session_repository,
                runs=run_repository,
                events=EventService(
                    events_repository,
                    InMemoryEventBus(),
                    clock=clock,
                    id_generator=lambda prefix: prefix + uuid4().hex,
                ),
                runtime=runtime,
                sandbox=sandbox,
                clock=clock,
                artifacts=artifacts,
                runtime_asset_stager=stage,
                sandbox_leases=sandbox_leases,
            )
            result = await worker.execute(tenant, run.run_id)
            assert result.status is RunStatus.SUCCEEDED, result.error_code
            return result

        try:
            await execute(0, FakeRuntime())
            try:
                await registry.get(tenant, user)
            except NotFoundError:
                report["plainChatAllocatedNoComputer"] = True
            else:
                raise AssertionError("plain chat unexpectedly allocated a Computer")
            await execute(1, ToolsRuntime(report))
            first = await registry.get(tenant, user)
            report.update(
                {
                    "computerId": first.computer_id,
                    "volumeRef": first.volume_ref,
                    "providerRef": first.provider_ref,
                }
            )
            await execute(2, ToolsRuntime(report))
            second = await registry.get(tenant, user)
            assert (first.computer_id, first.provider_ref, first.volume_ref) == (
                second.computer_id,
                second.provider_ref,
                second.volume_ref,
            )
            report["sameComputerAndDiskAcrossFreshProviders"] = True
            published = {}
            for index in (1, 2):
                published[str(index)] = [
                    e.payload["source_path"]
                    for e in await events_repository.list_after(
                        tenant, "fixture-run-" + str(index), 0
                    )
                    if e.type == "artifact.ready"
                ]
            assert published == {"1": ["outputs/first.txt"], "2": ["outputs/second.txt"]}, published
            report["publishedPerRound"] = published
            report["passed"] = True
        finally:
            try:
                computer = await registry.get(tenant, user)
            except NotFoundError:
                computer = None
            if computer is not None:
                report.update(
                    {
                        "volumeRef": computer.volume_ref,
                        "controlVolumeRef": computer.volume_ref + "-control",
                    }
                )
                token = await registry.claim_recovery(tenant, user, owner="fixture-cleanup")
                await driver.recover(computer, token)
                await registry.complete_recovery(token, provider_ref=None)
                report["fixtureInstanceRemoved"] = True
            output.write_text(json.dumps(report, indent=2) + "\n")
            await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(probe(args.config, args.output))
