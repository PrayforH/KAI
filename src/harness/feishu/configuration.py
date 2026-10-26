"""Encrypted deployment connection, optimistic updates and evidence-based diagnostics."""

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

import httpx
from pydantic import Field, SecretStr
from sqlalchemy import func, select

from harness.core.errors import ConflictError, NotFoundError
from harness.feishu.cards import text_message
from harness.feishu.models import FeishuSettings
from harness.feishu.repository import lock_key
from harness.feishu.service import FeishuService
from harness.feishu.storage import BindingRow, ConnectionRow, DeliveryRow, IncomingRow, ReceiverRow
from harness.storage.database import SessionFactory
from harness.storage.models import RunRow
from harness.studio.mcp_credential_store import McpCredentialCipher, StoredMcpCredential
from harness.studio.models import StudioModel

OWNER = "tenant:feishu-control-plane"
REFERENCE = "feishu-primary"


class ConfigureConnection(StudioModel):
    expected_revision: int = Field(alias="expectedRevision", ge=0)
    app_id: str = Field(alias="appId", pattern=r"^cli_[A-Za-z0-9]+$", max_length=128)
    app_secret: SecretStr | None = Field(default=None, alias="appSecret", max_length=16384)


class ConnectionRevision(StudioModel):
    expected_revision: int = Field(alias="expectedRevision", ge=0)


class ConnectionConfiguration(StudioModel):
    app_id: str = Field(alias="appId")
    configured: bool
    enabled: bool
    revision: int
    source: Literal["saved", "environment", "empty"]
    receiver_label: str = Field(alias="receiverLabel")
    applied_revision: int = Field(alias="appliedRevision")
    can_manage: bool = Field(default=False, alias="canManage")


class DiagnosticCheck(StudioModel):
    key: str
    label: str
    status: Literal["pass", "pending", "fail"]
    detail: str


class ConnectionDiagnostics(StudioModel):
    checked_at: datetime = Field(alias="checkedAt")
    checks: list[DiagnosticCheck]


class FeishuConnectionControl:
    def __init__(
        self,
        sessions: SessionFactory,
        cipher: McpCredentialCipher,
        legacy: FeishuSettings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.sessions, self.cipher, self.legacy, self.transport = (
            sessions,
            cipher,
            legacy,
            transport,
        )

    def _settings(self, row: ConnectionRow) -> FeishuSettings:
        stored = StoredMcpCredential(
            row.tenant_id,
            OWNER,
            REFERENCE,
            row.revision,
            ("app_secret",),
            row.ciphertext,
            row.updated_by,
            row.updated_at,
        )
        return FeishuSettings(
            **row.options,
            enabled=row.enabled,
            app_id=row.app_id,
            app_secret=self.cipher.decrypt(stored)["app_secret"],
            configuration_revision=row.revision,
        )

    async def resolve(self, tenant_id: str | None = None) -> FeishuSettings:
        async with self.sessions() as session:
            row = await session.get(ConnectionRow, "primary")
            if row is not None:
                if tenant_id is not None and row.tenant_id != tenant_id:
                    return FeishuSettings(enabled=False)
                return self._settings(row)
        return self.legacy

    async def get(self, tenant_id: str) -> ConnectionConfiguration:
        async with self.sessions() as session:
            row = await session.get(ConnectionRow, "primary")
            if row and row.tenant_id != tenant_id:
                raise NotFoundError("当前工作区没有飞书连接")
            settings = self._settings(row) if row else self.legacy
            receiver = await session.get(ReceiverRow, settings.app_id)
        return ConnectionConfiguration(
            appId=settings.app_id,
            configured=settings.configured,
            enabled=settings.enabled,
            revision=row.revision if row else 0,
            source="saved" if row else "environment" if settings.configured else "empty",
            receiverLabel=settings.receiver_label,
            appliedRevision=receiver.configuration_revision if receiver else 0,
        )

    async def validate_credentials(self, settings: FeishuSettings) -> bool:
        if not settings.configured:
            return False
        try:
            async with httpx.AsyncClient(
                transport=self.transport,
                trust_env=False,
                timeout=10,
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                    json={
                        "app_id": settings.app_id,
                        "app_secret": settings.app_secret.get_secret_value(),
                    },
                )
                response.raise_for_status()
                value = response.json()
                return value.get("code") == 0 and bool(value.get("tenant_access_token"))
        except (httpx.HTTPError, ValueError):
            return False

    async def configure(
        self,
        tenant_id: str,
        user_id: str,
        body: ConfigureConnection,
    ) -> ConnectionConfiguration:
        previous = await self.resolve(tenant_id)
        secret = body.app_secret
        if secret is None or not secret.get_secret_value().strip():
            if body.app_id != previous.app_id:
                raise ConflictError("更换应用时必须填写对应的 App Secret。")
            secret = previous.app_secret
        candidate = previous.model_copy(
            update={
                "enabled": True,
                "app_id": body.app_id,
                "app_secret": SecretStr(secret.get_secret_value().strip()),
            }
        )
        if not await self.validate_credentials(candidate):
            raise ConflictError("无法验证飞书凭据，请检查 App ID、App Secret 和服务器网络。")
        await self._save(tenant_id, user_id, candidate, body.expected_revision)
        return await self.get(tenant_id)

    async def _save(
        self,
        tenant_id: str,
        user_id: str,
        settings: FeishuSettings,
        expected_revision: int,
    ) -> None:
        async with self.sessions.begin() as session:
            await lock_key(session, "kai-feishu-connection-configuration")
            row = await session.get(ConnectionRow, "primary")
            if row and row.tenant_id != tenant_id:
                raise ConflictError("当前接收器已由另一个工作区配置。")
            if (row.revision if row else 0) != expected_revision:
                raise ConflictError("飞书配置已更新，请刷新后重试。")
            old_app = row.app_id if row else self.legacy.app_id
            if old_app and old_app != settings.app_id:
                bound = await session.scalar(
                    select(func.count())
                    .select_from(BindingRow)
                    .where(BindingRow.app_id == old_app, BindingRow.revoked_at.is_(None))
                )
                pending = await session.scalar(
                    select(func.count())
                    .select_from(IncomingRow)
                    .where(
                        IncomingRow.app_id == old_app,
                        IncomingRow.status.in_(["pending", "chat_pending", "chat_running"]),
                    )
                )
                if bound or pending:
                    raise ConflictError("原应用仍有账号绑定或待处理消息；请先处理后再更换应用。")
            now = datetime.now(UTC)
            ciphertext = self.cipher.encrypt(
                tenant_id, OWNER, REFERENCE, {"app_secret": settings.app_secret}
            )
            options = settings.model_dump(
                mode="json",
                exclude={
                    "app_id",
                    "app_secret",
                    "enabled",
                    "configuration_revision",
                },
            )
            if row is None:
                row = ConnectionRow(connection_id="primary", tenant_id=tenant_id, revision=0)
                session.add(row)
            row.app_id, row.enabled = settings.app_id, settings.enabled
            row.ciphertext, row.options = ciphertext, options
            row.revision += 1
            row.updated_by, row.updated_at = user_id, now

    async def disconnect(
        self,
        tenant_id: str,
        user_id: str,
        expected_revision: int,
    ) -> ConnectionConfiguration:
        settings = await self.resolve(tenant_id)
        if not settings.configured:
            raise ConflictError("尚未配置飞书连接。")
        await self._save(
            tenant_id, user_id, settings.model_copy(update={"enabled": False}), expected_revision
        )
        return await self.get(tenant_id)

    async def import_legacy(self, tenant_id: str, user_id: str) -> ConnectionConfiguration:
        current = await self.get(tenant_id)
        if current.source == "environment":
            await self._save(tenant_id, user_id, self.legacy, 0)
        return await self.get(tenant_id)

    async def diagnose(
        self, tenant_id: str, user_id: str, service: FeishuService
    ) -> ConnectionDiagnostics:
        configuration = await self.get(tenant_id)
        settings = await self.resolve(tenant_id)
        status = await service.status(tenant_id, user_id)
        credentials = await self.validate_credentials(settings)
        async with self.sessions() as session:
            saved = await session.get(ConnectionRow, "primary")
            since = saved.updated_at if saved else datetime.min.replace(tzinfo=UTC)
            binding = await service.repository.binding(session, tenant_id, user_id)
            delivered = None
            incoming = None
            run = None
            if binding:
                delivered = await session.scalar(
                    select(DeliveryRow)
                    .where(
                        DeliveryRow.binding_id == binding.binding_id,
                        DeliveryRow.created_at >= since,
                    )
                    .order_by(DeliveryRow.created_at.desc())
                    .limit(1)
                )
                incoming = await session.scalar(
                    select(IncomingRow)
                    .where(
                        IncomingRow.binding_id == binding.binding_id,
                        IncomingRow.run_id.is_not(None),
                        IncomingRow.created_at >= since,
                    )
                    .order_by(IncomingRow.created_at.desc())
                    .limit(1)
                )
                if incoming and incoming.run_id:
                    run = await session.get(RunRow, (tenant_id, incoming.run_id))
                media = await session.scalar(
                    select(IncomingRow)
                    .where(
                        IncomingRow.binding_id == binding.binding_id,
                        IncomingRow.payload["prepared"]["artifacts"].is_not(None),
                        IncomingRow.created_at >= since,
                    )
                    .order_by(IncomingRow.created_at.desc())
                    .limit(1)
                )
            else:
                media = None
        applied = (
            configuration.applied_revision
            == configuration.revision
            == settings.configuration_revision
        )
        received = bool(status.last_event_at and status.last_event_at >= since)
        online = (
            settings.enabled
            and status.receiver_online
            and applied
            and status.receiver_state in {"awaiting_event", "receiving"}
        )
        attachment = bool(
            media
            and media.payload.get("prepared", {})
            and media.payload["prepared"].get("artifacts")
        )
        checks = [
            DiagnosticCheck(
                key="credentials",
                label="应用凭据",
                status="pass" if credentials else "fail",
                detail="App ID 和 Secret 验证成功。"
                if credentials
                else "请检查凭据和服务器到飞书的网络。",
            ),
            DiagnosticCheck(
                key="receiver",
                label="长连接",
                status="pass" if online else "pending",
                detail=f"{settings.receiver_label} 已连接。"
                if online
                else "等待接收器应用配置并连接；也请确认飞书后台选择了长连接。",
            ),
            DiagnosticCheck(
                key="events",
                label="消息接收",
                status="pass" if received else "pending",
                detail=f"本次配置已收到事件：{status.last_event_at.isoformat()}"
                if received and status.last_event_at
                else "在飞书给 KAI 发一句话；如未收到，请检查事件订阅、可用范围和发布状态。",
            ),
            DiagnosticCheck(
                key="binding",
                label="本人账号",
                status="pass" if binding else "pending",
                detail="飞书已绑定到你的 KAI 账号。"
                if binding
                else "请生成绑定码，在机器人私聊发送。",
            ),
            DiagnosticCheck(
                key="reply",
                label="机器人回复",
                status="pass"
                if delivered and delivered.status == "sent"
                else "fail"
                if delivered and delivered.status in {"failed", "uncertain"}
                else "pending",
                detail=f"本次配置已投递回复：{delivered.created_at.isoformat()}"
                if delivered and delivered.status == "sent"
                else "可发送测试消息；失败时检查机器人发信权限。",
            ),
            DiagnosticCheck(
                key="chat",
                label="助理聊天",
                status="pass" if run and run.status == "succeeded" else "pending",
                detail="最近一轮助理已成功处理消息。"
                if run and run.status == "succeeded"
                else "请从飞书发一条聊天消息，等待助理完成。",
            ),
            DiagnosticCheck(
                key="attachments",
                label="图片与文件",
                status="pass" if attachment else "pending",
                detail=f"本次配置已读取附件：{media.created_at.isoformat()}"
                if attachment and media
                else "发送图片或文件验证；如失败，请检查消息读取权限。",
            ),
        ]
        return ConnectionDiagnostics(checkedAt=datetime.now(UTC), checks=checks)

    async def send_test(self, tenant_id: str, user_id: str, service: FeishuService) -> str:
        await service.authorize(tenant_id, user_id)
        if not service.settings.enabled:
            raise ConflictError("请先连接飞书机器人。")
        async with self.sessions.begin() as session:
            binding = await service.repository.binding(session, tenant_id, user_id, lock=True)
            if not binding:
                raise ConflictError("请先绑定你的飞书账号。")
            from harness.feishu.repository import binding_value

            key = "connection-test:" + uuid4().hex
            await service.repository.reply(
                session,
                key=key,
                actor=binding_value(binding).actor,
                binding=binding,
                content=text_message(
                    "KAI 飞书连接测试：机器人回复已送达。网页与飞书共用你的助理会话。"
                ),
                now=datetime.now(UTC),
                category="connection_test",
            )
        return key

    async def test_status(
        self,
        tenant_id: str,
        user_id: str,
        service: FeishuService,
        request_id: str,
    ) -> dict[str, str | None]:
        async with self.sessions() as session:
            binding = await service.repository.binding(session, tenant_id, user_id)
            if binding:
                row = await session.scalar(
                    select(DeliveryRow).where(
                        DeliveryRow.app_id == service.settings.app_id,
                        DeliveryRow.binding_id == binding.binding_id,
                        DeliveryRow.category == "connection_test",
                        DeliveryRow.dedup_key == request_id,
                    )
                )
                if row:
                    return {"status": row.status, "messageId": row.message_id}
        raise NotFoundError("找不到本人连接测试")
