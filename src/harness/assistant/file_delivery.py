"""Durable per-artifact channel delivery; upload and message send are separate steps.

A retry never reruns the assistant. Upload references are encrypted at rest and
binding/owner access is rechecked under lock before either network operation.
Feishu's documented UUID window allows resend; an interrupted Weixin send stays
uncertain because its protocol does not promise an exactly-once receipt.
"""

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from pydantic import SecretStr
from sqlalchemy import DateTime, Index, Integer, String, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from harness.application.artifacts import ArtifactService
from harness.core.errors import NotFoundError, PermissionDeniedError
from harness.core.models import Artifact, ArtifactStatus
from harness.feishu.media import MediaPermissionError, MediaRejectedError
from harness.storage.database import SessionFactory
from harness.storage.models import Base
from harness.studio.mcp_credential_store import McpCredentialCipher, StoredMcpCredential


class ChannelFileRow(Base):
    __tablename__ = "assistant_channel_files"
    __table_args__ = (
        Index("ix_assistant_channel_files_work", "channel", "status", "next_attempt_at"),
        Index(
            "ix_assistant_channel_files_reference", "channel", "tenant_id", "user_id", "message_id"
        ),
    )
    delivery_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel: Mapped[str] = mapped_column(String(16))
    tenant_id: Mapped[str] = mapped_column(String(128))
    user_id: Mapped[str] = mapped_column(String(128))
    source_id: Mapped[str] = mapped_column(String(128))
    route: Mapped[str] = mapped_column(String(256))
    run_id: Mapped[str] = mapped_column(String(128))
    artifact_id: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    ciphertext: Mapped[str] = mapped_column(String, default="")
    # Public transport receipt for reverse lookup; upload credentials stay encrypted.
    message_id: Mapped[str | None] = mapped_column(String(128))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    first_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FileTransport(Protocol):
    channel: str
    retry_send: bool
    route_prefix: str

    async def target(self, session: AsyncSession, row: ChannelFileRow) -> Any | None: ...
    async def upload(self, target: Any, artifact: Artifact, content: bytes) -> dict[str, Any]: ...
    async def send(self, target: Any, payload: dict[str, Any], request_id: str) -> str | None: ...


class ChannelFiles:
    def __init__(
        self,
        sessions: SessionFactory,
        artifacts: ArtifactService,
        cipher: McpCredentialCipher,
        transport: FileTransport,
    ) -> None:
        self.sessions, self.artifacts, self.cipher, self.transport = (
            sessions,
            artifacts,
            cipher,
            transport,
        )

    async def enqueue(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        user_id: str,
        source_id: str,
        route: str,
        run_id: str,
    ) -> int:
        artifacts = await self.artifacts.list_for_run(tenant_id, run_id)
        count = 0
        for artifact in artifacts:
            if artifact.status is not ArtifactStatus.READY:
                continue
            key = hashlib.sha256(
                f"{self.transport.channel}:{source_id}:{artifact.artifact_id}".encode()
            ).hexdigest()[:32]
            instant = datetime.now(UTC)
            await session.execute(
                insert(ChannelFileRow)
                .values(
                    delivery_id="file_" + key,
                    channel=self.transport.channel,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    source_id=source_id,
                    route=route,
                    run_id=run_id,
                    artifact_id=artifact.artifact_id,
                    status="pending",
                    ciphertext="",
                    attempts=0,
                    created_at=instant,
                    next_attempt_at=instant,
                )
                .on_conflict_do_nothing(index_elements=[ChannelFileRow.delivery_id])
            )
            count += 1
        return count

    def seal(self, row: ChannelFileRow, payload: dict[str, Any]) -> str:
        return self.cipher.encrypt(
            row.tenant_id, row.user_id, row.delivery_id, {"payload": SecretStr(json.dumps(payload))}
        )

    async def counts(self, session: AsyncSession, tenant: str, user: str) -> dict[str, int]:
        result = await session.execute(
            select(ChannelFileRow.status, func.count())
            .where(
                ChannelFileRow.channel == self.transport.channel,
                ChannelFileRow.route.startswith(self.transport.route_prefix),
                ChannelFileRow.tenant_id == tenant,
                ChannelFileRow.user_id == user,
            )
            .group_by(ChannelFileRow.status)
        )
        return {state: count for state, count in result.all()}

    async def retry_failed(self, tenant: str, user: str) -> int:
        """Explicit owner retry; never resend a delivery with an unknown receipt."""
        async with self.sessions.begin() as session:
            rows = (
                await session.scalars(
                    select(ChannelFileRow)
                    .where(
                        ChannelFileRow.channel == self.transport.channel,
                        ChannelFileRow.route.startswith(self.transport.route_prefix),
                        ChannelFileRow.tenant_id == tenant,
                        ChannelFileRow.user_id == user,
                        ChannelFileRow.status.in_(["blocked", "failed"]),
                    )
                    .with_for_update()
                )
            ).all()
            for row in rows:
                row.status = "ready" if row.ciphertext else "pending"
                row.attempts, row.error_code = 0, None
                row.next_attempt_at = datetime.now(UTC)
            return len(rows)

    def unseal(self, row: ChannelFileRow) -> dict[str, Any]:
        stored = StoredMcpCredential(
            row.tenant_id,
            row.user_id,
            row.delivery_id,
            1,
            (),
            row.ciphertext,
            row.user_id,
            row.created_at,
        )
        return json.loads(self.cipher.decrypt(stored)["payload"].get_secret_value())

    async def deliver_once(self, instant: datetime | None = None) -> bool:
        instant = instant or datetime.now(UTC)
        identifier = None
        async with self.sessions.begin() as session:
            row = await session.scalar(
                select(ChannelFileRow)
                .where(
                    ChannelFileRow.channel == self.transport.channel,
                    ChannelFileRow.route.startswith(self.transport.route_prefix),
                    ChannelFileRow.status.in_(["pending", "ready", "sending"]),
                    ChannelFileRow.next_attempt_at <= instant,
                )
                .order_by(ChannelFileRow.created_at, ChannelFileRow.delivery_id)
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            if row is None:
                return False
            if row.status == "sending":
                if not self.transport.retry_send:
                    row.status, row.error_code = "uncertain", "DeliveryInterrupted"
                    return True
                row.status = "ready"
            if row.first_attempt_at and row.first_attempt_at + timedelta(minutes=50) <= instant:
                row.status, row.error_code = "uncertain", "DedupWindowElapsed"
                return True
            try:
                async with asyncio.timeout(60):
                    target = await self.transport.target(session, row)
                    if target is None:
                        row.status, row.error_code = "skipped", "BindingUnavailable"
                        return True
                    if row.status == "pending":
                        artifact = await self.artifacts.get(row.tenant_id, row.artifact_id)
                        if (
                            artifact.run_id != row.run_id
                            or artifact.status is not ArtifactStatus.READY
                        ):
                            raise MediaRejectedError("文件尚未就绪，不能发送。")
                        artifact, content = await self.artifacts.download(
                            row.tenant_id, row.artifact_id
                        )
                        payload = await self.transport.upload(target, artifact, content)
                        row.ciphertext, row.status, row.error_code = (
                            self.seal(row, payload),
                            "ready",
                            None,
                        )
                        return True  # Persist upload before attempting a message.
                    row.status, row.first_attempt_at = "sending", row.first_attempt_at or instant
                    row.next_attempt_at = instant + timedelta(seconds=90)
                    identifier = row.delivery_id
            except (NotFoundError, PermissionDeniedError):
                row.status, row.error_code = "skipped", "OwnerUnavailable"
            except MediaPermissionError:
                row.status, row.error_code = "blocked", "UploadPermissionRequired"
            except MediaRejectedError:
                row.status, row.error_code = "failed", "FileRejected"
            except Exception:
                self.retry(row, instant, "UploadUnavailable")
        if identifier:
            # 'sending' is durable before network I/O. Revocation is serialized
            # with the binding lock held by target() until send has completed.
            async with self.sessions.begin() as session:
                row = await session.get(ChannelFileRow, identifier, with_for_update=True)
                if row is None or row.status != "sending":
                    return True
                try:
                    async with asyncio.timeout(60):
                        target = await self.transport.target(session, row)
                        if target is None:
                            row.status, row.error_code = "skipped", "BindingUnavailable"
                            return True
                        payload = self.unseal(row)
                        receipt = await self.transport.send(target, payload, row.delivery_id)
                        if isinstance(receipt, str) and receipt:
                            row.message_id = receipt
                            row.ciphertext = self.seal(row, {**payload, "message_id": receipt})
                        row.status, row.error_code = "sent", None
                except (NotFoundError, PermissionDeniedError):
                    row.status, row.error_code = "skipped", "OwnerUnavailable"
                except Exception:
                    if self.transport.retry_send:
                        self.retry(row, instant, "SendUnavailable", state="ready")
                    else:
                        row.status, row.error_code = "uncertain", "SendNotConfirmed"
        return True

    @staticmethod
    def retry(
        row: ChannelFileRow, instant: datetime, error: str, *, state: str = "pending"
    ) -> None:
        row.attempts += 1
        row.error_code = error
        row.status = "failed" if row.attempts >= 6 else state
        row.next_attempt_at = instant + timedelta(seconds=min(300, 2**row.attempts))
