"""Dedicated long-connection process: python -m harness.feishu.main.

The SDK owns the main thread's event loop. A separate loop owns the database;
callbacks wait only for durable ingestion, never model execution or delivery.
"""
# pyright: reportMissingTypeStubs=false
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import os
import signal
import sys
import threading
from datetime import UTC, datetime
from uuid import uuid4

import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
from lark_oapi.event.callback.model.p2_card_action_trigger import (
    P2CardActionTrigger,
    P2CardActionTriggerResponse,
)
from sqlalchemy import text

from harness.composition import build_production_container
from harness.config import Settings
from harness.feishu.delivery import FeishuDelivery
from harness.feishu.models import FeishuSettings
from harness.feishu.protocol import normalize
from harness.feishu.sdk import SdkSender
from harness.feishu.service import FeishuService

logger = logging.getLogger(__name__)


class Receiver:
    def __init__(self, settings: FeishuSettings) -> None:
        self.settings = settings
        self.loop = asyncio.new_event_loop()
        self.ready: concurrent.futures.Future[FeishuService] = concurrent.futures.Future()
        self.stop: asyncio.Event | None = None
        self.instance = uuid4().hex
        self.state = "connecting"
        self.received = False

    def mark(self, state: str, received: bool = False) -> None:
        def apply() -> None:
            self.state = state
            self.received = self.received or received

        self.loop.call_soon_threadsafe(apply)

    def ingest(self, data: P2ImMessageReceiveV1 | P2CardActionTrigger) -> bool:
        incoming = normalize(json.loads(lark.JSON.marshal(data) or "{}"), self.settings.app_id)
        if incoming is None:
            return False
        service = self.ready.result(timeout=0)
        try:
            future = asyncio.run_coroutine_threadsafe(
                service.repository.ingest(incoming, datetime.now(UTC)), self.loop
            )
            accepted = future.result(timeout=2)
        except Exception:
            # SDK logs the exception text; do not let SQL parameters or raw events
            # reach that logger. A late DB commit is safe under event deduplication.
            raise RuntimeError("KAI event persistence unavailable") from None
        self.mark("receiving", received=True)
        return accepted

    def message(self, data: P2ImMessageReceiveV1) -> None:
        self.ingest(data)

    def action(self, data: P2CardActionTrigger) -> P2CardActionTriggerResponse:
        accepted = self.ingest(data)
        return P2CardActionTriggerResponse(
            {
                "toast": {
                    "type": "info",
                    "content": "已收到，处理结果将私聊通知。"
                    if accepted
                    else "暂未受理，请稍后重试。",
                }
            }
        )

    async def heartbeats(self, service: FeishuService) -> None:
        assert self.stop is not None
        while not self.stop.is_set():
            received, self.received = self.received, False
            await service.repository.heartbeat(
                self.instance, self.state, datetime.now(UTC), received=received,
                configuration_revision=self.settings.configuration_revision,
            )
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=5)
            except TimeoutError:
                pass

    async def work(self) -> None:
        self.stop = asyncio.Event()
        container = build_production_container(Settings(), execution_enabled=False)
        service = (
            container.feishu_factory(self.settings)
            if container.feishu_factory
            else container.feishu
        )
        assert service is not None
        try:
            async with service.repository.sessions.begin() as leader:
                acquired = await leader.scalar(
                    text("SELECT pg_try_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {"key": "kai-feishu-receiver:" + self.settings.app_id},
                )
                if not acquired:
                    raise RuntimeError("A receiver already owns this app")
                delivery = FeishuDelivery(
                    service.repository, self.settings, SdkSender(self.settings), service.authorize
                )
                self.ready.set_result(service)
                async with asyncio.TaskGroup() as tasks:
                    # Downloads/ASR may take longer than the online TTL. Keep
                    # liveness independent while the durable work remains serial.
                    tasks.create_task(self.heartbeats(service))
                    while not self.stop.is_set():
                        for _ in range(10):
                            if not await service.process_once():
                                break
                        for _ in range(10):
                            if not await service.process_conversation_once():
                                break
                        if service.files:
                            for _ in range(4):
                                if not await service.files.deliver_once():
                                    break
                        await delivery.collect()
                        for _ in range(2):
                            if not await delivery.deliver_once():
                                break
                        try:
                            await asyncio.wait_for(self.stop.wait(), timeout=1)
                        except TimeoutError:
                            pass
        finally:
            if container.close:
                await container.close()

    def background(self) -> None:
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self.work())
        except Exception:
            if not self.ready.done():
                self.ready.set_exception(RuntimeError("Feishu worker startup failed"))
            else:
                # Fail the whole process so the supervisor reconnects, rather than
                # keeping a live SDK socket whose callbacks cannot be persisted.
                logger.error("Feishu worker stopped; restart required")
                os.kill(os.getpid(), signal.SIGTERM)
        finally:
            self.loop.close()

    def shutdown(self) -> None:
        if self.stop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.stop.set)


def entrypoint() -> None:
    logging.basicConfig(level=logging.WARNING)
    settings = (FeishuSettings.model_validate_json(sys.stdin.buffer.read())
                if os.getenv("HARNESS_FEISHU_MANAGED_CHILD") == "1" else FeishuSettings())
    if not settings.enabled:
        raise SystemExit("Feishu is disabled; configure the dedicated app before starting receiver")
    receiver = Receiver(settings)
    thread = threading.Thread(target=receiver.background, name="kai-feishu-db", daemon=True)
    thread.start()
    receiver.ready.result(timeout=45)
    handler = (
        lark.EventDispatcherHandler.builder("", "")
        .register_p2_im_message_receive_v1(receiver.message)
        .register_p2_card_action_trigger(receiver.action)
        .build()
    )
    class ObservedClient(lark.ws.Client):
        async def _connect(self) -> None:
            receiver.mark("connecting")
            await super()._connect()
            receiver.mark("awaiting_event")

    client = ObservedClient(
        settings.app_id,
        settings.app_secret.get_secret_value(),
        event_handler=handler,
        log_level=lark.LogLevel.WARNING,
    )
    client.on_reconnecting = lambda: receiver.mark("reconnecting")
    client.on_reconnected = lambda: receiver.mark("awaiting_event")
    try:
        client.start()
    finally:
        receiver.shutdown()
        thread.join(timeout=15)


if __name__ == "__main__":
    entrypoint()
