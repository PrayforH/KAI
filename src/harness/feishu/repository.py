"""Identity and channel storage. Domain changes join these transactions explicitly."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from harness.core.errors import ConflictError, NotFoundError
from harness.feishu.models import Actor, Binding, BindingCode, Incoming, MessageContent
from harness.feishu.storage import BindingCodeRow, BindingRow, DeliveryRow, IncomingRow, ReceiverRow
from harness.storage.database import SessionFactory


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def actor_key(actor: Actor) -> str:
    return digest(f"{actor.app_id}\0{actor.tenant_key}\0{actor.open_id}")


async def lock_key(session: AsyncSession, key: str) -> None:
    # Always transaction-scoped. Sorted lock order is documented at call sites.
    number = int.from_bytes(hashlib.sha256(key.encode()).digest()[:8], "big", signed=True)
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": number})


def binding_value(row: BindingRow) -> Binding:
    return Binding(
        binding_id=row.binding_id,
        actor=Actor(app_id=row.app_id, tenant_key=row.tenant_key, open_id=row.open_id),
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        notifications=row.notifications,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
    )


class FeishuRepository:
    def __init__(self, sessions: SessionFactory, app_id: str) -> None:
        self.sessions, self.app_id = sessions, app_id

    def owner_key(self, tenant_id: str, user_id: str) -> str:
        return f"feishu-owner:{self.app_id}:{tenant_id}:{user_id}"

    async def binding(
        self, session: AsyncSession, tenant_id: str, user_id: str, *, lock: bool = False
    ) -> BindingRow | None:
        query = select(BindingRow).where(
            BindingRow.app_id == self.app_id,
            BindingRow.tenant_id == tenant_id,
            BindingRow.user_id == user_id,
            BindingRow.revoked_at.is_(None),
        )
        return await session.scalar(query.with_for_update() if lock else query)

    async def actor_binding(
        self, session: AsyncSession, actor: Actor, *, lock: bool = False
    ) -> BindingRow | None:
        if actor.app_id != self.app_id:
            return None
        query = select(BindingRow).where(
            BindingRow.app_id == actor.app_id,
            BindingRow.tenant_key == actor.tenant_key,
            BindingRow.open_id == actor.open_id,
            BindingRow.revoked_at.is_(None),
        )
        return await session.scalar(query.with_for_update() if lock else query)

    async def issue_code(self, tenant_id: str, user_id: str, now: datetime) -> BindingCode:
        code = secrets.token_hex(12).upper()
        async with self.sessions.begin() as session:
            await lock_key(session, self.owner_key(tenant_id, user_id))
            if await self.binding(session, tenant_id, user_id):
                raise ConflictError("Unbind the existing Feishu identity first")
            recent = await session.scalar(
                select(func.max(BindingCodeRow.created_at)).where(
                    BindingCodeRow.app_id == self.app_id,
                    BindingCodeRow.tenant_id == tenant_id,
                    BindingCodeRow.user_id == user_id,
                )
            )
            if recent and recent > now - timedelta(seconds=30):
                raise ConflictError("Wait 30 seconds before generating another binding code")
            await session.execute(
                update(BindingCodeRow)
                .where(
                    BindingCodeRow.app_id == self.app_id,
                    BindingCodeRow.tenant_id == tenant_id,
                    BindingCodeRow.user_id == user_id,
                    BindingCodeRow.consumed_at.is_(None),
                )
                .values(consumed_at=now)
            )
            expires = now + timedelta(minutes=10)
            session.add(
                BindingCodeRow(
                    digest=digest(code),
                    app_id=self.app_id,
                    tenant_id=tenant_id,
                    user_id=user_id,
                    created_at=now,
                    expires_at=expires,
                )
            )
        return BindingCode(code=code, expiresAt=expires)

    async def consume_code(
        self, session: AsyncSession, actor: Actor, code_digest: str, now: datetime
    ) -> BindingRow:
        # Read owner without a row lock, then lock actor -> owner -> code. This
        # avoids a cycle with issue_code/unbind, which lock owner -> code.
        code = await session.scalar(
            select(BindingCodeRow).where(
                BindingCodeRow.digest == code_digest, BindingCodeRow.app_id == self.app_id
            )
        )
        if code is None or actor.app_id != self.app_id:
            raise ConflictError("绑定码无效或已过期，请在 KAI 重新生成。")
        await lock_key(session, f"feishu-actor:{actor_key(actor)}")
        await lock_key(session, self.owner_key(code.tenant_id, code.user_id))
        await session.refresh(code, with_for_update=True)
        if code.consumed_at is not None or code.expires_at <= now:
            raise ConflictError("绑定码无效或已过期，请在 KAI 重新生成。")
        if await self.actor_binding(session, actor) or await self.binding(
            session, code.tenant_id, code.user_id
        ):
            raise ConflictError("已有绑定，请先在 KAI 解绑，再重新生成绑定码。")
        row = BindingRow(
            binding_id=f"feishu_binding_{uuid4().hex}",
            app_id=self.app_id,
            tenant_key=actor.tenant_key,
            open_id=actor.open_id,
            tenant_id=code.tenant_id,
            user_id=code.user_id,
            notifications=True,
            created_at=now,
        )
        session.add(row)
        code.consumed_at = now
        await session.flush()
        return row

    async def unbind(self, tenant_id: str, user_id: str, now: datetime) -> None:
        async with self.sessions.begin() as session:
            await lock_key(session, self.owner_key(tenant_id, user_id))
            row = await self.binding(session, tenant_id, user_id, lock=True)
            if row:
                row.revoked_at = now
            await session.execute(
                update(BindingCodeRow)
                .where(
                    BindingCodeRow.app_id == self.app_id,
                    BindingCodeRow.tenant_id == tenant_id,
                    BindingCodeRow.user_id == user_id,
                    BindingCodeRow.consumed_at.is_(None),
                )
                .values(consumed_at=now)
            )
            # Pending delivery is checked under the binding lock at send time.
            # No bulk update here: that would invert the sender's lock order.

    async def preferences(self, tenant_id: str, user_id: str, enabled: bool) -> None:
        async with self.sessions.begin() as session:
            row = await self.binding(session, tenant_id, user_id, lock=True)
            if row is None:
                raise NotFoundError("Feishu binding not found")
            row.notifications = enabled

    async def ingest(self, incoming: Incoming, now: datetime) -> bool:
        if incoming.actor.app_id != self.app_id:
            return False
        key = actor_key(incoming.actor)
        async with self.sessions.begin() as session:
            await lock_key(session, f"feishu-rate:{key}")
            # Duplicates are acknowledged even after the rate limit is reached.
            if await session.scalar(
                select(IncomingRow.incoming_id).where(
                    IncomingRow.app_id == self.app_id, IncomingRow.event_key == incoming.key
                )
            ):
                return True
            count = await session.scalar(
                select(func.count())
                .select_from(IncomingRow)
                .where(
                    IncomingRow.app_id == self.app_id,
                    IncomingRow.actor_key == key,
                    IncomingRow.created_at > now - timedelta(minutes=1),
                )
            )
            if (count or 0) >= 30:
                return False
            await session.execute(
                insert(IncomingRow)
                .values(
                    incoming_id=f"incoming_{uuid4().hex}",
                    app_id=self.app_id,
                    actor_key=key,
                    event_key=incoming.key,
                    payload=incoming.model_dump(mode="json"),
                    status="pending",
                    attempts=0,
                    created_at=now,
                    next_attempt_at=now,
                )
                .on_conflict_do_nothing(index_elements=["app_id", "event_key"])
            )
        return True

    async def reply(
        self,
        session: AsyncSession,
        *,
        key: str,
        actor: Actor,
        binding: BindingRow | None,
        content: MessageContent,
        now: datetime,
        category: str = "reply",
        status: str = "pending",
        objective_id: str | None = None,
        fingerprint: str | None = None,
    ) -> None:
        # A deterministic UUID doubles as Feishu's one-hour request dedup key.
        delivery_id = digest(f"{self.app_id}\0{key}")[:40]
        await session.execute(
            insert(DeliveryRow)
            .values(
                delivery_id=delivery_id,
                app_id=self.app_id,
                binding_id=binding.binding_id if binding else None,
                actor=actor.model_dump(),
                dedup_key=key,
                payload=content.model_dump(mode="json"),
                objective_id=objective_id,
                fingerprint=fingerprint,
                category=category,
                status=status,
                attempts=0,
                created_at=now,
                next_attempt_at=now,
            )
            .on_conflict_do_nothing(index_elements=["app_id", "dedup_key"])
        )

    async def heartbeat(
        self, instance_id: str, state: str, now: datetime, *, received: bool = False,
        configuration_revision: int = 0,
    ) -> None:
        async with self.sessions.begin() as session:
            values = {"instance_id": instance_id, "state": state, "heartbeat_at": now,
                      "configuration_revision": configuration_revision}
            if received:
                values["last_event_at"] = now
            await session.execute(
                insert(ReceiverRow)
                .values(
                    app_id=self.app_id,
                    last_event_at=now if received else None,
                    instance_id=instance_id,
                    state=state,
                    heartbeat_at=now,
                    configuration_revision=configuration_revision,
                )
                .on_conflict_do_update(index_elements=["app_id"], set_=values)
            )
