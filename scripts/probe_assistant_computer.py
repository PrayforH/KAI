"""Destructive component acceptance on a dedicated Computer probe database.

Uses only generated fixture identities and files. It does not submit a model
Run or touch real chat/binding rows. The private JSON config supplies an
isolated database, control-service address/secret and installed Computer image.
"""

import argparse
import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import httpx
from sqlalchemy import Table, select
from sqlalchemy.engine import make_url

from harness.computer.control import HttpComputerControl
from harness.computer.docker import DockerComputerDriver
from harness.computer.manager import ComputerAllocation, ComputerManager
from harness.computer.models import ComputerLeaseLostError
from harness.computer.registry import ComputerRegistry
from harness.computer.storage import AssistantComputerRow, ComputerLeaseRow
from harness.storage.database import create_database


async def probe(config_path: Path, output_path: Path) -> None:
    config = json.loads(config_path.read_text())
    if make_url(config["database_url"]).database != "harness_computer_probe":
        raise ValueError("this probe requires the isolated harness_computer_probe database")
    engine, sessions = create_database(config["database_url"])
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
    client = httpx.AsyncClient(
        base_url=config["control_url"],
        headers={"Authorization": "Bearer " + config["secret"]},
        trust_env=False,
    )
    host = HttpComputerControl(client)
    driver = DockerComputerDriver(host, image=config["image"])
    registry = ComputerRegistry(sessions, lease_ttl_seconds=20)
    manager = ComputerManager(registry, driver, owner="probe-worker-a", heartbeat_seconds=1)
    tenant, user = "computer-probe-" + uuid4().hex, "fixture-owner"
    report: dict[str, Any] = {
        "tenant": tenant,
        "checkedAt": datetime.now(UTC).isoformat(),
        "scope": "Controlled Manager executions, not canonical KAI model Runs or real user chats",
    }

    def stage(value: str) -> None:
        print(json.dumps({"stage": value}), flush=True)

    async def run(allocation: ComputerAllocation, source: str):
        result = await driver.execute(allocation, ["python3", "-c", source])
        if result.exit_code:
            raise RuntimeError("controlled fixture command failed")
        return result.stdout

    async def daemon(allocation: ComputerAllocation, name: str) -> int:
        child = (
            "import time,os; from pathlib import Path; "
            f"p=Path({name!r}); q=Path({(name + '.tmp')!r}); "
            "\nwhile True:\n q.write_text(str(time.monotonic())); os.replace(q,p); time.sleep(.05)"
        )
        source = (
            "import subprocess,time; from pathlib import Path; "
            f"p=subprocess.Popen(['python3','-c',{child!r}],start_new_session=True, "
            "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
            f"target=Path({name!r}); "
            "\nfor _ in range(100):\n"
            " if target.exists(): break\n time.sleep(.01)\nprint(p.pid)"
        )
        return int(await run(allocation, source))

    try:
        stage("first_execution")
        async with manager.execution(
            tenant, user, run_id="fixture-r1", session_id="fixture-s"
        ) as first:
            report["computer_id"] = first.computer.computer_id
            report["volume_ref"] = first.computer.volume_ref
            report["initial_instance"] = first.computer.provider_ref
            value = json.loads(
                await run(
                    first,
                    "import os,json,socket; from pathlib import Path; "
                    "Path('retained.txt').write_text('KAI-COMPUTER79-V0'); "
                    "p=Path.home()/'bin'/'kai-fixture'; "
                    "p.write_text('#!/bin/sh\\nprintf KAI-TOOL79-V0'); p.chmod(0o700); "
                    "s=socket.socket(); code=s.connect_ex(('127.0.0.1',44772)); s.close(); "
                    "print(json.dumps({'uids':os.getresuid(),'root_control_readable':os.access('/var/lib/kai-computer/state.json',os.R_OK),"
                    "'docker_socket_visible':Path('/var/run/docker.sock').exists(),'execd_unreachable':code!=0}))",
                )
            )
            assert value["uids"] == [28000, 28000, 28000]
            assert not value["root_control_readable"] and not value["docker_socket_visible"]
            assert value["execd_unreachable"]
            report["task_privileges"] = value
            report["first_daemon"] = await daemon(first, "round1.counter")
            await asyncio.sleep(2.2)
            async with sessions() as db:
                lease = await db.get(ComputerLeaseRow, first.token.lease_id)
                assert lease is not None and lease.expires_at > first.token.expires_at
            report["heartbeat_confirmed"] = True
        first_finished = time.monotonic()
        await client.aclose()
        await asyncio.sleep(1)

        stage("fresh_manager_reconnect")
        client = httpx.AsyncClient(
            base_url=config["control_url"],
            headers={"Authorization": "Bearer " + config["secret"]},
            trust_env=False,
        )
        host = HttpComputerControl(client)
        driver = DockerComputerDriver(host, image=config["image"])
        manager = ComputerManager(
            ComputerRegistry(sessions, lease_ttl_seconds=20),
            driver,
            owner="probe-worker-b",
            heartbeat_seconds=1,
        )
        async with manager.execution(
            tenant, user, run_id="fixture-r2", session_id="fixture-s"
        ) as second:
            assert second.computer.provider_ref == first.computer.provider_ref
            assert second.computer.computer_id == first.computer.computer_id
            result = json.loads(
                await run(
                    second,
                    "import json,subprocess; from pathlib import Path; "
                    "print(json.dumps({'retained':Path('retained.txt').read_text(),"
                    "'tool':subprocess.check_output([str(Path.home()/'bin'/'kai-fixture')],text=True),"
                    "'last_writer':float(Path('round1.counter').read_text())}))",
                )
            )
            assert result["retained"] == "KAI-COMPUTER79-V0" and result["tool"] == "KAI-TOOL79-V0"
            assert result["last_writer"] <= first_finished + 0.1
            try:
                await run(first, "from pathlib import Path; Path('stale.txt').write_text('bad')")
            except ComputerLeaseLostError:
                report["stale_holder_rejected"] = True
            else:
                raise AssertionError("stale holder was accepted")
            report["fresh_manager_same_instance_and_disk"] = True
            report["finish_drained_detached_daemon"] = True

        stage("crashed_worker_lease")
        token = await registry.claim_execution(
            tenant,
            user,
            owner="fixture-crashed-worker",
            run_id="fixture-crash",
            session_id="fixture-s",
        )
        abandoned = ComputerAllocation(await registry.get(tenant, user), token)
        await driver.connect(abandoned.computer, token)
        report["crash_daemon"] = await daemon(abandoned, "crash.counter")
        expires_mono = time.monotonic() + (token.expires_at - datetime.now(UTC)).total_seconds()
        # No Manager heartbeat or finally: simulate a vanished worker. The root
        # watchdog must close this execution even after its last command ended.
        while datetime.now(UTC).timestamp() < token.expires_at.timestamp() + 3:
            await asyncio.sleep(0.5)

        stage("physical_recovery")
        async with manager.execution(
            tenant, user, run_id="fixture-r3", session_id="fixture-s"
        ) as third:
            assert third.computer.computer_id == first.computer.computer_id
            assert third.computer.volume_ref == first.computer.volume_ref
            assert third.computer.provider_ref != first.computer.provider_ref
            recovered = json.loads(
                await run(
                    third,
                    "import json; from pathlib import Path; "
                    "print(json.dumps({'retained':Path('retained.txt').read_text(),"
                    "'last_writer':float(Path('crash.counter').read_text()),'stale_file_exists':Path('stale.txt').exists()}))",
                )
            )
            assert recovered["retained"] == "KAI-COMPUTER79-V0"
            assert recovered["last_writer"] <= expires_mono + 1
            assert not recovered["stale_file_exists"]
            report["replacement_instance"] = third.computer.provider_ref
            report["expired_worker_stopped_before_recovery"] = True
            report["recovery_preserved_identity_and_disk"] = True
        report["passed"] = True
    finally:
        stage("cleanup")
        try:
            async with sessions() as db:
                live = await db.scalar(
                    select(ComputerLeaseRow).where(
                        ComputerLeaseRow.tenant_id == tenant,
                        ComputerLeaseRow.user_id == user,
                        ComputerLeaseRow.state == "active",
                    )
                )
            if live is not None:
                wait = (live.expires_at - datetime.now(UTC)).total_seconds()
                if wait > 0:
                    await asyncio.sleep(min(wait + 0.1, 21))
            cleanup = await registry.claim_recovery(tenant, user, owner="fixture-cleanup")
            await host.lifecycle(cleanup, "reset")
            final = await registry.complete_recovery(cleanup, provider_ref=None)
            report["volume_ref"] = final.volume_ref
            report["control_volume_ref"] = final.volume_ref + "-control"
            report["test_instance_removed"] = True
        finally:
            await client.aclose()
            await engine.dispose()
            output_path.write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    asyncio.run(probe(arguments.config, arguments.output))
