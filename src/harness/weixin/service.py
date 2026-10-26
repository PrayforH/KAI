"""Personal QR pairing, encrypted credentials, durable ingestion and shared chat."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from io import BytesIO
from typing import Any, cast
from uuid import uuid4

import qrcode  # type: ignore[import-untyped]
import qrcode.image.svg
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert

from harness.assistant.conversation import PersonalConversation
from harness.auth.service import AuthService
from harness.core.errors import ConflictError, NotFoundError, PermissionDeniedError
from harness.feishu.media import MediaRejectedError
from harness.feishu.models import PreparedMessage
from harness.storage.database import SessionFactory
from harness.studio.mcp_credential_store import McpCredentialCipher, StoredMcpCredential
from harness.weixin.client import BASE_URL, WeixinClient, WeixinError, trusted_base
from harness.weixin.media import WeixinMedia
from harness.weixin.storage import WeixinConnectionRow as Connection
from harness.weixin.storage import WeixinMessageRow as Message


def objects(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        cast(dict[str, Any], item) for item in cast(list[object], value) if isinstance(item, dict)
    ]


def now() -> datetime:
    return datetime.now(UTC)


def qr_svg(value: str) -> str:
    output = BytesIO()
    qrcode.make(value, image_factory=qrcode.image.svg.SvgPathImage, box_size=6, border=4).save(
        output
    )
    return output.getvalue().decode()


class WeixinService:
    def __init__(
        self,
        sessions: SessionFactory,
        cipher: McpCredentialCipher,
        conversation: PersonalConversation,
        auth: AuthService,
        client: WeixinClient | None = None,
        *,
        media: WeixinMedia | None = None,
    ) -> None:
        self.sessions, self.cipher, self.conversation, self.auth = (
            sessions,
            cipher,
            conversation,
            auth,
        )
        self.client = client or WeixinClient()
        self.media = media

    def seal(self, row: Connection, purpose: str, value: dict[str, str]) -> str:
        return self.cipher.encrypt(
            row.tenant_id,
            row.user_id,
            "weixin-" + purpose,
            {k: SecretStr(v) for k, v in value.items()},
        )

    def open(self, row: Connection, purpose: str, ciphertext: str) -> dict[str, str]:
        if not ciphertext:
            return {}
        stored = StoredMcpCredential(
            row.tenant_id,
            row.user_id,
            "weixin-" + purpose,
            1,
            (),
            ciphertext,
            row.user_id,
            row.updated_at,
        )
        return {k: v.get_secret_value() for k, v in self.cipher.decrypt(stored).items()}

    async def status(self, tenant: str, user: str) -> dict[str, Any]:
        async with self.sessions() as s:
            row = await s.get(Connection, (tenant, user))
            if not row:
                return {
                    "bound": False,
                    "enabled": False,
                    "online": False,
                    "state": "disconnected",
                    "loginStatus": None,
                }
            pending = row.login_status in {"wait", "scaned", "need_verifycode"}
            expired = pending and (not row.login_expires_at or row.login_expires_at <= now())
            login = self.open(row, "login", row.login_ciphertext) if pending and not expired else {}
            counts = {
                str(state): int(count)
                for state, count in (
                    await s.execute(
                        select(Message.status, func.count())
                        .where(
                            Message.tenant_id == tenant,
                            Message.user_id == user,
                            Message.generation == row.generation,
                        )
                        .group_by(Message.status)
                    )
                ).all()
            }
            return {
                "bound": bool(row.ciphertext),
                "enabled": row.enabled,
                "online": bool(
                    row.enabled
                    and row.heartbeat_at
                    and row.heartbeat_at > now() - timedelta(seconds=60)
                    and row.state == "connected"
                ),
                "state": row.state,
                "loginId": row.login_id,
                "loginStatus": "expired" if expired else row.login_status,
                "expiresAt": row.login_expires_at,
                "qrSvg": qr_svg(login["url"]) if login else None,
                "lastEventAt": row.last_event_at,
                "lastReplyAt": row.last_reply_at,
                "errorCode": row.error_code,
                "pendingMessages": sum(
                    counts.get(k, 0) for k in ("pending", "running", "ready", "sending")
                ),
                "failedMessages": sum(counts.get(k, 0) for k in ("failed", "uncertain")),
                "receiverLabel": "79 · 微信接收器",
            }

    async def authorize(self, tenant: str, user: str) -> None:
        account, membership = await self.auth.profile(tenant, user)
        if account.disabled or membership.role not in {"owner", "admin", "member"}:
            raise PermissionDeniedError("当前 KAI 账号无法使用微信助理")

    async def start(self, tenant: str, user: str) -> dict[str, Any]:
        await self.authorize(tenant, user)
        async with self.sessions.begin() as s:
            await s.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": "weixin-login:" + tenant + ":" + user},
            )
            row = await s.get(Connection, (tenant, user), with_for_update=True)
            if (
                row
                and row.login_expires_at
                and row.login_expires_at > now()
                and row.login_status in {"wait", "scaned", "need_verifycode"}
            ):
                pass
            else:
                try:
                    result = await self.client.qr()
                except WeixinError:
                    raise ConflictError("无法生成微信二维码，请检查服务器网络后重试。") from None
                if not all(
                    isinstance(result.get(k), str) and result[k]
                    for k in ("qrcode", "qrcode_img_content")
                ):
                    raise ConflictError("微信未返回可用二维码，请稍后重试。")
                if row is None:
                    row = Connection(
                        tenant_id=tenant, user_id=user, generation=uuid4().hex, updated_at=now()
                    )
                    s.add(row)
                row.login_id, row.login_status = uuid4().hex, "wait"
                row.login_expires_at = now() + timedelta(minutes=5)
                row.login_ciphertext = self.seal(
                    row,
                    "login",
                    {
                        "qrcode": result["qrcode"],
                        "url": result["qrcode_img_content"],
                        "base": BASE_URL,
                        "verify": "",
                    },
                )
        return await self.status(tenant, user)

    async def verify(self, tenant: str, user: str, login_id: str, code: str) -> dict[str, Any]:
        async with self.sessions.begin() as s:
            row = await s.get(Connection, (tenant, user), with_for_update=True)
            if (
                not row
                or row.login_id != login_id
                or row.login_status != "need_verifycode"
                or (not row.login_expires_at or row.login_expires_at <= now())
            ):
                raise ConflictError("微信二维码状态已变化，请重新连接。")
            data = self.open(row, "login", row.login_ciphertext)
            data["verify"] = code
            row.login_ciphertext = self.seal(row, "login", data)
            row.login_status = "scaned"
        return await self.status(tenant, user)

    async def disconnect(self, tenant: str, user: str) -> None:
        async with self.sessions.begin() as s:
            row = await s.get(Connection, (tenant, user), with_for_update=True)
            if row:
                row.enabled, row.state = False, "disconnected"
                row.login_status, row.login_ciphertext, row.login_id = None, "", None
                row.updated_at = now()

    async def login_once(self, tenant: str, user: str) -> None:
        async with self.sessions() as s:
            snapshot = await s.get(Connection, (tenant, user))
            if (
                snapshot
                and snapshot.login_status in {"wait", "scaned", "need_verifycode"}
                and snapshot.login_expires_at
                and snapshot.login_expires_at <= now()
            ):
                async with self.sessions.begin() as expired_session:
                    expired = await expired_session.get(
                        Connection, (tenant, user), with_for_update=True
                    )
                    if expired and expired.login_id == snapshot.login_id:
                        expired.login_status, expired.login_ciphertext = "expired", ""
                return
            if not snapshot or snapshot.login_status not in {"wait", "scaned"}:
                return
            login_id = snapshot.login_id
            data = self.open(snapshot, "login", snapshot.login_ciphertext)
        try:
            result = await self.client.qr_status(
                data["qrcode"], data["base"], data.get("verify", "")
            )
        except WeixinError:
            return
        async with self.sessions.begin() as s:
            row = await s.get(Connection, (tenant, user), with_for_update=True)
            if not row or row.login_id != login_id or row.login_status not in {"wait", "scaned"}:
                return
            if not row.login_expires_at or row.login_expires_at <= now():
                row.login_status, row.login_ciphertext = "expired", ""
                return
            state = result.get("status")
            if state == "scaned_but_redirect":
                try:
                    data["base"] = trusted_base(result.get("redirect_host", ""))
                except WeixinError:
                    row.login_status, row.login_ciphertext = "failed", ""
                    return
                data["verify"] = ""
                row.login_ciphertext = self.seal(row, "login", data)
            elif state == "confirmed":
                if not all(
                    isinstance(result.get(k), str) and result[k]
                    for k in ("bot_token", "ilink_bot_id", "ilink_user_id", "baseurl")
                ):
                    row.login_status, row.login_ciphertext = "failed", ""
                    return
                try:
                    base = trusted_base(result["baseurl"])
                except WeixinError:
                    row.login_status, row.login_ciphertext = "failed", ""
                    return
                bot_key = hashlib.sha256(result["ilink_bot_id"].encode()).hexdigest()
                await s.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                    {"key": "weixin-bot:" + bot_key},
                )
                existing = await s.scalar(
                    select(Connection).where(
                        Connection.bot_key == bot_key,
                        Connection.enabled,
                        (Connection.tenant_id != tenant) | (Connection.user_id != user),
                    )
                )
                if existing:
                    row.login_status, row.login_ciphertext = "failed", ""
                    return
                row.bot_key = bot_key
                # Explicit QR scan pairs only the scanning person to this signed-in KAI account.
                row.generation = uuid4().hex
                row.ciphertext = self.seal(
                    row,
                    "credentials",
                    {
                        "token": result["bot_token"],
                        "bot": result["ilink_bot_id"],
                        "peer": result["ilink_user_id"],
                        "base": base,
                    },
                )
                row.cursor_ciphertext = ""
                row.enabled, row.state, row.error_code = True, "connecting", None
                row.heartbeat_at = row.last_event_at = row.last_reply_at = None
                row.updated_at, row.login_status, row.login_ciphertext = now(), "confirmed", ""
            elif state in {"expired", "verify_code_blocked", "binded_redirect"}:
                # Never claim a new binding without receiving verifiable credentials.
                row.login_status, row.login_ciphertext = "expired", ""
            elif state in {"wait", "scaned", "need_verifycode"}:
                row.login_status = state
                data["verify"] = ""
                row.login_ciphertext = self.seal(row, "login", data)

    async def receive(self, tenant: str, user: str) -> None:
        async with self.sessions() as s:
            snapshot = await s.get(Connection, (tenant, user))
            if not snapshot or not snapshot.enabled:
                return
            generation = snapshot.generation
            credentials = self.open(snapshot, "credentials", snapshot.ciphertext)
            cursor = self.open(snapshot, "cursor", snapshot.cursor_ciphertext).get("cursor", "")
        try:
            result = await self.client.updates(credentials, cursor)
        except WeixinError as error:
            async with self.sessions.begin() as s:
                row = await s.get(Connection, (tenant, user), with_for_update=True)
                if row and row.enabled and row.generation == generation:
                    row.state, row.error_code = "reconnecting", error.code
                    if error.code == "AuthorizationExpired":
                        row.enabled, row.state = False, "authorization_expired"
            return
        await self.ingest(tenant, user, generation, credentials, result)

    async def ingest(
        self,
        tenant: str,
        user: str,
        generation: str,
        credentials: dict[str, str],
        result: dict[str, Any],
    ) -> None:
        async with self.sessions.begin() as s:
            row = await s.get(Connection, (tenant, user), with_for_update=True)
            if not row or not row.enabled or row.generation != generation:
                return
            row.state, row.heartbeat_at, row.error_code = "connected", now(), None
            for msg in objects(result.get("msgs")):
                if (
                    msg.get("message_type") != 1
                    or msg.get("from_user_id") != credentials["peer"]
                    or msg.get("group_id")
                ):
                    continue
                identifier, context = msg.get("message_id"), msg.get("context_token")
                if identifier is None or not isinstance(context, str) or not context:
                    continue
                parts = objects(msg.get("item_list"))
                content = "\n".join(
                    str(item["text_item"]["text"])
                    for item in parts
                    if item.get("type") == 1
                    and isinstance(item.get("text_item"), dict)
                    and isinstance(item["text_item"].get("text"), str)
                )
                attachments = [item for item in parts if item.get("type") != 1]
                payload = json.dumps(attachments, ensure_ascii=False)
                reply = (
                    "微信消息内容过长或附件过多，请拆分后发送。"
                    if len(content) > (8000 if attachments else 30000)
                    or len(attachments) > 10
                    or len(payload) > 64000
                    else None
                )
                if not content.strip() and not attachments and not reply:
                    continue
                key = hashlib.sha256(
                    (credentials["bot"] + ":" + str(identifier)).encode()
                ).hexdigest()
                await s.execute(
                    insert(Message)
                    .values(
                        message_id=uuid4().hex,
                        message_key=key,
                        tenant_id=tenant,
                        user_id=user,
                        generation=generation,
                        text=content,
                        media_ciphertext=self.seal(row, "media", {"items": payload})
                        if attachments and not reply
                        else "",
                        context_ciphertext=self.seal(row, "context", {"context": context}),
                        status="ready" if reply else "pending",
                        reply=reply,
                        attempts=0,
                        created_at=now(),
                    )
                    .on_conflict_do_nothing(index_elements=["message_key"])
                )
                row.last_event_at = now()
            cursor = result.get("get_updates_buf")
            if isinstance(cursor, str) and cursor:
                row.cursor_ciphertext = self.seal(row, "cursor", {"cursor": cursor})

    async def prepare_first(self, tenant: str, user: str) -> bool:
        # Download/ASR outside the connection lock so long polling can keep committing.
        async with self.sessions() as s:
            connection = await s.get(Connection, (tenant, user))
            if not connection or not connection.enabled:
                return False
            message = await s.scalar(
                select(Message)
                .where(
                    Message.tenant_id == tenant,
                    Message.user_id == user,
                    Message.generation == connection.generation,
                    Message.status.in_(["pending", "running", "ready", "sending"]),
                )
                .order_by(Message.created_at, Message.message_id)
                .limit(1)
            )
            if (
                not message
                or message.status != "pending"
                or not message.media_ciphertext
                or message.prepared
            ):
                return True
            payload = self.open(connection, "media", message.media_ciphertext)["items"]
        prepared = None
        rejection = None
        retry = False
        try:
            await self.authorize(tenant, user)
            if self.media is None:
                raise MediaRejectedError("微信附件处理尚未启用，请管理员检查配置。")
            prepared = await self.media.prepare(
                tenant, user, message.message_id, message.text, payload
            )
        except MediaRejectedError as error:
            rejection = str(error)
        except (ConnectionError, TimeoutError, OSError):
            retry = True
        except (NotFoundError, PermissionDeniedError):
            return False
        async with self.sessions.begin() as s:
            active = await s.get(Connection, (tenant, user), with_for_update=True)
            row = await s.get(Message, message.message_id, with_for_update=True)
            if (
                not active
                or not active.enabled
                or active.generation != message.generation
                or not row
                or row.status != "pending"
                or row.run_id
            ):
                return False
            if prepared:
                row.prepared, row.error_code = prepared.model_dump(mode="json"), None
            elif retry:
                row.attempts += 1
                if row.attempts < 3:
                    return False
                row.reply, row.status, row.error_code = (
                    "微信附件暂时无法下载或识别，请稍后重新发送。",
                    "ready",
                    "MediaUnavailable",
                )
            else:
                row.reply, row.status, row.error_code = rejection, "ready", "MediaRejected"
        return True

    async def process_once(self, tenant: str, user: str) -> bool:
        if not await self.prepare_first(tenant, user):
            return False
        async with self.sessions.begin() as s:
            connection = await s.get(Connection, (tenant, user), with_for_update=True)
            if not connection or not connection.enabled:
                return False
            row = await s.scalar(
                select(Message)
                .where(
                    Message.tenant_id == tenant,
                    Message.user_id == user,
                    Message.generation == connection.generation,
                    Message.status.in_(["pending", "running", "ready", "sending"]),
                )
                .order_by(Message.created_at, Message.message_id)
                .limit(1)
                .with_for_update()
            )
            if not row:
                return False
            if row.status == "sending":
                row.status, row.error_code = "uncertain", "DeliveryInterrupted"
                return True
            try:
                await self.authorize(tenant, user)
                if row.status in {"pending", "running"}:
                    prepared = (
                        PreparedMessage.model_validate(row.prepared) if row.prepared else None
                    )
                    if row.run_id:
                        run = await self.conversation.runs.get(tenant, row.run_id)
                    elif prepared:
                        run = await self.conversation.submit(
                            tenant,
                            user,
                            "weixin-chat-" + row.message_key,
                            prepared.text,
                            artifacts=prepared.artifacts,
                            model_route=prepared.model_route,
                        )
                    else:
                        run = await self.conversation.submit(
                            tenant,
                            user,
                            "weixin-chat-" + row.message_key,
                            row.text,
                        )
                    row.run_id = run.run_id
                    if not run.status.is_terminal:
                        row.status = "running"
                        return True
                    row.reply = await self.conversation.answer(run)
                    row.status = "ready"
                credentials = self.open(connection, "credentials", connection.ciphertext)
                context = self.open(connection, "context", row.context_ciphertext)["context"]
                identifier, reply = (
                    row.message_id,
                    row.reply or "本次未返回文字结果，请在网页查看。",
                )
                row.status = "sending"
            except ConflictError:
                # The Web or Feishu can be running a turn; retain this distinct message in order.
                return False
            except (NotFoundError, PermissionDeniedError):
                row.status, row.error_code = "failed", "AccountUnavailable"
                return True
        # Committed 'sending' before I/O: a crash must not blindly duplicate a sent reply.
        async with self.sessions() as check:
            active = await check.get(Connection, (tenant, user))
            if not active or not active.enabled or active.generation != row.generation:
                return True
        try:
            await self.authorize(tenant, user)
        except (NotFoundError, PermissionDeniedError):
            return True
        try:
            await self.client.send(credentials, context, "kai-wx-" + identifier, reply)
            state, error_code = "done", None
        except WeixinError as error:
            state, error_code = "uncertain", error.code
        async with self.sessions.begin() as s:
            row = await s.get(Message, identifier, with_for_update=True)
            assert row is not None
            row.status, row.error_code, row.finished_at = state, error_code or row.error_code, now()
            if state == "done":
                connection = await s.get(Connection, (tenant, user), with_for_update=True)
                if connection and connection.generation == row.generation:
                    connection.last_reply_at = now()
        return True

    async def diagnose(self, tenant: str, user: str) -> dict[str, Any]:
        status = await self.status(tenant, user)
        async with self.sessions() as s:
            connection = await s.get(Connection, (tenant, user))
            latest = (
                await s.scalar(
                    select(Message)
                    .where(
                        Message.tenant_id == tenant,
                        Message.user_id == user,
                        Message.generation == connection.generation,
                    )
                    .order_by(Message.created_at.desc())
                    .limit(1)
                )
                if connection
                else None
            )
        run = (
            await self.conversation.runs.get(tenant, latest.run_id)
            if latest and latest.run_id
            else None
        )
        async with self.sessions() as s:
            media_message = (
                await s.scalar(
                    select(Message)
                    .where(
                        Message.tenant_id == tenant,
                        Message.user_id == user,
                        Message.generation == connection.generation,
                        Message.media_ciphertext != "",
                    )
                    .order_by(Message.created_at.desc())
                    .limit(1)
                )
                if connection
                else None
            )
        checks = [
            (
                "binding",
                "微信绑定",
                bool(status["bound"] and status["enabled"]),
                "扫码授权与当前 KAI 账号关联。",
            ),
            ("receiver", "消息连接", status["online"], "接收器已完成微信长轮询。"),
            ("incoming", "消息接收", bool(status.get("lastEventAt")), "本次连接已收到本人消息。"),
            (
                "chat",
                "助理会话",
                bool(run and run.status.value == "succeeded"),
                "消息已进入与飞书、网页共用的助理会话。",
            ),
            (
                "reply",
                "消息回复",
                bool(latest and latest.status == "done" and status.get("lastReplyAt")),
                "微信接口已确认接收回复。",
            ),
        ]
        if media_message:
            checks.append(
                (
                    "media",
                    "图片、文件与语音",
                    bool(media_message.prepared),
                    "附件已读取并保存到当前账号的助理会话。",
                )
            )
        return {
            "checkedAt": now(),
            "checks": [
                {
                    "key": key,
                    "label": label,
                    "status": "pass" if passed else "pending",
                    "detail": detail
                    if passed
                    else "附件处理未完成，请查看消息回复或重新发送附件。"
                    if key == "media"
                    else "请扫码连接，然后向微信助理发送消息验证。",
                }
                for key, label, passed, detail in checks
            ],
        }
