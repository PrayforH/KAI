"""Dedicated Weixin receiver: one deployment leader, isolated owner workers."""

import asyncio
import logging
import signal
from collections.abc import Awaitable, Callable

from sqlalchemy import or_, select, text

from harness.composition import build_production_container
from harness.config import Settings
from harness.weixin.service import WeixinService
from harness.weixin.storage import WeixinConnectionRow

logger = logging.getLogger(__name__)


async def owner_loop(service: WeixinService, tenant: str, user: str) -> None:
    async def loop(operation: Callable[[str, str], Awaitable[object]], interval: float) -> None:
        while True:
            try:
                await operation(tenant, user)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Never include exception details: database parameters may contain private text.
                logger.warning("Weixin channel operation failed; retry scheduled")
            await asyncio.sleep(interval)

    async with asyncio.TaskGroup() as tasks:
        tasks.create_task(loop(service.login_once, 1))
        tasks.create_task(loop(service.receive, 2))
        tasks.create_task(loop(service.process_chat_once, 1))
        # Media download/ASR can be slow; stop/status must remain reachable.
        tasks.create_task(loop(service.process_control_once, 1))
        if service.files:

            async def files_once(_tenant: str, _user: str) -> object:
                assert service.files is not None
                return await service.files.deliver_once()

            tasks.create_task(loop(files_once, 1))


async def run() -> None:
    container = build_production_container(Settings(), execution_enabled=False)
    service = container.weixin
    assert service is not None
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    workers: dict[tuple[str, str], asyncio.Task[None]] = {}
    try:
        async with service.sessions.begin() as leader:
            acquired = await leader.scalar(
                text("SELECT pg_try_advisory_xact_lock(hashtextextended('kai-weixin-receiver', 0))")
            )
            if not acquired:
                raise RuntimeError("A Weixin receiver already owns this deployment")
            while not stop.is_set():
                await leader.execute(text("SELECT 1"))
                async with service.sessions() as s:
                    owners = {
                        (tenant, user)
                        for tenant, user in (
                            await s.execute(
                                select(
                                    WeixinConnectionRow.tenant_id, WeixinConnectionRow.user_id
                                ).where(
                                    or_(
                                        WeixinConnectionRow.enabled,
                                        WeixinConnectionRow.login_status.in_(
                                            ["wait", "scaned", "need_verifycode"]
                                        ),
                                    )
                                )
                            )
                        ).all()
                    }
                for owner in list(workers):
                    if owner not in owners or workers[owner].done():
                        workers.pop(owner).cancel()
                for owner in owners:
                    if owner not in workers:
                        workers[owner] = asyncio.create_task(owner_loop(service, *owner))
                try:
                    await asyncio.wait_for(stop.wait(), 2)
                except TimeoutError:
                    pass
    finally:
        for task in workers.values():
            task.cancel()
        await asyncio.gather(*workers.values(), return_exceptions=True)
        if container.close:
            await container.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(run())
