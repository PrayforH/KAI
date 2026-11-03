"""Bounded delivery retries with a durable clock preceding every network request."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import uuid4

from sqlalchemy import exists, or_, select

from harness.assistant.models import AssistantInboxItem, NotificationMode, Objective
from harness.assistant.notice_fingerprint import notice_fingerprint
from harness.assistant.storage import AssistantInboxRow, ObjectiveRow
from harness.core.errors import NotFoundError, PermissionDeniedError
from harness.feishu.cards import card
from harness.feishu.models import Actor, FeishuSettings, MessageContent
from harness.feishu.repository import FeishuRepository, binding_value
from harness.feishu.storage import BindingRow, DeliveryRow
from harness.storage.models import ArtifactRow


class Sender(Protocol):
    async def send(self, actor: Actor, content: MessageContent, request_id: str) -> str: ...


class DeliveryRejectedError(Exception):
    """A definitive platform rejection. Never carries external payloads or secrets."""


class FeishuDelivery:
    def __init__(
        self,
        repository: FeishuRepository,
        settings: FeishuSettings,
        sender: Sender,
        authorize: Callable[[str, str], Awaitable[None]],
    ) -> None:
        self.repository, self.settings, self.sender, self.authorize = (
            repository,
            settings,
            sender,
            authorize,
        )

    async def collect(self, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        count = 0
        async with self.repository.sessions.begin() as session:
            key = "notice:" + BindingRow.binding_id + ":" + AssistantInboxRow.item_id
            rows = (
                await session.execute(
                    select(AssistantInboxRow, BindingRow, ObjectiveRow)
                    .join(
                        BindingRow,
                        (BindingRow.tenant_id == AssistantInboxRow.tenant_id)
                        & (BindingRow.user_id == AssistantInboxRow.user_id),
                    )
                    .join(ObjectiveRow, ObjectiveRow.objective_id == AssistantInboxRow.objective_id)
                    .where(
                        BindingRow.app_id == self.repository.app_id,
                        BindingRow.revoked_at.is_(None),
                        AssistantInboxRow.created_at >= BindingRow.created_at,
                        ~exists(
                            select(DeliveryRow.delivery_id).where(
                                DeliveryRow.app_id == self.repository.app_id,
                                DeliveryRow.dedup_key == key,
                            )
                        ),
                    )
                    .order_by(AssistantInboxRow.created_at, AssistantInboxRow.item_id)
                    .limit(20)
                )
            ).all()
            for notice, binding, objective in rows:
                item = AssistantInboxItem.model_validate(notice.payload)
                obj = Objective.model_validate(objective.payload)
                # 成果签名（名称+类型+内容哈希）在该 Run 有产物时纳入指纹；
                # 查不到就不贡献，绝不伪造。
                artifact_signatures: list[tuple[str, str, str]] = []
                if item.run_id:
                    artifact_rows = await session.scalars(
                        select(ArtifactRow)
                        .where(
                            ArtifactRow.tenant_id == notice.tenant_id,
                            ArtifactRow.run_id == item.run_id,
                        )
                        .order_by(ArtifactRow.artifact_id)
                        .limit(20)
                    )
                    for artifact_row in artifact_rows:
                        payload = artifact_row.payload or {}
                        artifact_signatures.append(
                            (
                                str(payload.get("name", "")),
                                str(payload.get("mediaType") or payload.get("media_type") or ""),
                                str(payload.get("sha256") or ""),
                            )
                        )
                fingerprint = notice_fingerprint(
                    kind=item.kind,
                    objective_status=obj.status.value,
                    execution_id=item.execution_id,
                    run_id=item.run_id,
                    source_event_id=item.source_event_id,
                    source_sequence=item.source_sequence,
                    artifacts=artifact_signatures,
                )
                previous = await session.scalar(
                    select(DeliveryRow)
                    .where(
                        DeliveryRow.binding_id == binding.binding_id,
                        DeliveryRow.objective_id == obj.objective_id,
                        DeliveryRow.category == "notification",
                        DeliveryRow.status.in_(["pending", "sent", "uncertain"]),
                    )
                    .order_by(DeliveryRow.created_at.desc())
                    .limit(1)
                )
                duplicate = bool(
                    previous and previous.fingerprint == fingerprint and item.kind == "succeeded"
                )
                skip = (
                    not binding.notifications
                    or obj.notification_mode is NotificationMode.NONE
                    or (obj.notification_mode is NotificationMode.IMPORTANT and duplicate)
                )
                await self.repository.reply(
                    session,
                    key=f"notice:{binding.binding_id}:{item.item_id}",
                    actor=binding_value(binding).actor,
                    binding=binding,
                    content=card(
                        item.title,
                        item.content or item.kind,
                        self.settings.public_base_url,
                        objective_id=obj.objective_id,
                    ),
                    now=now,
                    category="notification",
                    status="skipped" if skip else "pending",
                    objective_id=obj.objective_id,
                    fingerprint=fingerprint,
                )
                count += 1
        return count

    async def claim(self, now: datetime) -> tuple[str, str] | None:
        # Commit first_attempt_at BEFORE sending. A crash after the network request
        # cannot reset the platform's one-hour deduplication window on recovery.
        async with self.repository.sessions.begin() as session:
            row = await session.scalar(
                select(DeliveryRow)
                .where(
                    DeliveryRow.app_id == self.repository.app_id,
                    DeliveryRow.status == "pending",
                    DeliveryRow.next_attempt_at <= now,
                    or_(DeliveryRow.lease_until.is_(None), DeliveryRow.lease_until <= now),
                )
                .order_by(DeliveryRow.created_at, DeliveryRow.delivery_id)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if row is None:
                return None
            if row.first_attempt_at and row.first_attempt_at + timedelta(minutes=50) <= now:
                row.status, row.error_code = "uncertain", "DedupWindowElapsed"
                return None
            row.first_attempt_at = row.first_attempt_at or now
            token = uuid4().hex
            row.lease_token, row.lease_until = token, now + timedelta(seconds=30)
            row.attempts += 1
            return row.delivery_id, token

    async def deliver_once(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(UTC)
        claimed = await self.claim(now)
        if claimed is None:
            return False
        delivery_id, token = claimed
        async with self.repository.sessions.begin() as session:
            row = await session.scalar(
                select(DeliveryRow).where(DeliveryRow.delivery_id == delivery_id).with_for_update()
            )
            if (
                row is None
                or row.status != "pending"
                or row.lease_token != token
                or not row.lease_until
                or row.lease_until <= now
            ):
                return True
            if row.binding_id:
                binding = await session.scalar(
                    select(BindingRow)
                    .where(BindingRow.binding_id == row.binding_id)
                    .with_for_update()
                )
                if (
                    binding is None
                    or binding.revoked_at
                    or (row.category == "notification" and not binding.notifications)
                ):
                    row.status = "skipped"
                    return True
                if row.category == "notification" and row.objective_id:
                    objective = await session.get(ObjectiveRow, row.objective_id)
                    if (
                        objective is None
                        or Objective.model_validate(objective.payload).notification_mode
                        is NotificationMode.NONE
                    ):
                        row.status = "skipped"
                        return True
                try:
                    await self.authorize(binding.tenant_id, binding.user_id)
                except (NotFoundError, PermissionDeniedError):
                    row.status, row.error_code = "skipped", "OwnerUnavailable"
                    return True
            try:
                # Binding remains locked until send completes. Unbind waits for an
                # already-started send; after unbind commits no private send starts.
                async with asyncio.timeout(12):
                    row.message_id = await self.sender.send(
                        Actor.model_validate(row.actor),
                        MessageContent.model_validate(row.payload),
                        row.delivery_id,
                    )
                row.status = "sent"
                row.error_code = None
            except DeliveryRejectedError:
                row.status, row.error_code = "failed", "PlatformRejected"
            except Exception:
                row.error_code = "TransportUnavailable"
                row.next_attempt_at = now + timedelta(seconds=min(300, 2 ** min(row.attempts, 9)))
                if row.attempts >= 12:
                    row.status = "uncertain"
            row.lease_token, row.lease_until = None, None
        return True
