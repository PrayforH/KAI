"""One managed child per deployment; credentials travel only over a private stdin pipe."""

import asyncio
import json
import logging
import os
import signal
import sys

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from harness.composition import build_production_container
from harness.config import Settings
from harness.feishu.configuration import FeishuConnectionControl
from harness.feishu.models import FeishuSettings

logger = logging.getLogger(__name__)


def child_payload(settings: FeishuSettings) -> bytes:
    value = settings.model_dump(mode="json", exclude={"app_secret"})
    value["app_secret"] = settings.app_secret.get_secret_value()
    return json.dumps(value).encode()


async def stop_child(child: asyncio.subprocess.Process | None) -> None:
    if child is None or child.returncode is not None:
        return
    try:
        child.terminate()
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(child.wait(), 12)
    except TimeoutError:
        child.kill()
        await child.wait()


async def supervise(
    control: FeishuConnectionControl,
    stop: asyncio.Event,
    *,
    leader: AsyncSession | None = None,
    poll_seconds: float = 2,
) -> None:
    child = None
    loop = asyncio.get_running_loop()
    try:
        active = None
        retry_at = 0.0
        failures = 0
        while not stop.is_set():
            if leader is not None:
                await leader.execute(text("SELECT 1"))
            settings = await control.resolve()
            desired = (settings.app_id, settings.enabled, settings.configuration_revision)
            if desired != active:
                await stop_child(child)
                child, active, retry_at, failures = None, desired, 0.0, 0
            if (
                settings.enabled
                and settings.configured
                and (child is None or child.returncode is not None)
                and loop.time() >= retry_at
            ):
                if child is not None:
                    failures += 1
                environment = dict(os.environ, HARNESS_FEISHU_MANAGED_CHILD="1")
                for key in ("HARNESS_FEISHU_APP_SECRET", "HARNESS_FEISHU_APP_ID"):
                    environment.pop(key, None)
                child = await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "harness.feishu.main",
                    stdin=asyncio.subprocess.PIPE,
                    env=environment,
                )
                assert child.stdin is not None
                child.stdin.write(child_payload(settings))
                await child.stdin.drain()
                child.stdin.close()
                retry_at = loop.time() + min(30, 2 ** min(failures, 5))
            try:
                await asyncio.wait_for(stop.wait(), poll_seconds)
            except TimeoutError:
                pass
    finally:
        await stop_child(child)


async def run() -> None:
    container = build_production_container(Settings(), execution_enabled=False)
    control = container.feishu_connections
    assert control is not None
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    try:
        async with control.sessions.begin() as leader:
            acquired = await leader.scalar(
                text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": "kai-feishu-connection-supervisor"},
            )
            if not acquired:
                raise RuntimeError("A connection supervisor already owns this deployment")
            await supervise(control, stop, leader=leader)
    finally:
        if container.close:
            await container.close()


def entrypoint() -> None:
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(run())


if __name__ == "__main__":
    entrypoint()
