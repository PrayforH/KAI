"""PostgreSQL persistence for scoped API integration keys."""

from datetime import datetime

from sqlalchemy import select

from harness.auth.api_access import ApiAccessKey
from harness.storage.database import SessionFactory
from harness.storage.models import ApiAccessKeyRow


def _value(row: ApiAccessKeyRow) -> ApiAccessKey:
    return ApiAccessKey(
        key_id=row.key_id,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        name=row.name,
        prefix=row.prefix,
        token_hash=row.token_hash,
        permissions=tuple(str(item) for item in row.permissions),
        created_at=row.created_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
    )


class PostgresApiAccessKeyRepository:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def create(self, value: ApiAccessKey) -> ApiAccessKey:
        async with self._sessions() as session:
            row = ApiAccessKeyRow(
                key_id=value.key_id,
                tenant_id=value.tenant_id,
                user_id=value.user_id,
                name=value.name,
                prefix=value.prefix,
                token_hash=value.token_hash,
                permissions=list(value.permissions),
                created_at=value.created_at,
                last_used_at=value.last_used_at,
                revoked_at=value.revoked_at,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return _value(row)

    async def get_by_hash(self, token_hash: str) -> ApiAccessKey | None:
        async with self._sessions() as session:
            row = await session.scalar(
                select(ApiAccessKeyRow).where(ApiAccessKeyRow.token_hash == token_hash)
            )
            return _value(row) if row is not None else None

    async def list_for_tenant(self, tenant_id: str) -> tuple[ApiAccessKey, ...]:
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(ApiAccessKeyRow)
                    .where(ApiAccessKeyRow.tenant_id == tenant_id)
                    .order_by(ApiAccessKeyRow.created_at.desc())
                )
            ).all()
            return tuple(_value(row) for row in rows)

    async def revoke(
        self, tenant_id: str, key_id: str, revoked_at: datetime
    ) -> ApiAccessKey | None:
        async with self._sessions() as session:
            row = await session.get(ApiAccessKeyRow, key_id)
            if row is None or row.tenant_id != tenant_id:
                return None
            row.revoked_at = revoked_at
            await session.commit()
            await session.refresh(row)
            return _value(row)

    async def touch(self, key_id: str, used_at: datetime) -> None:
        async with self._sessions() as session:
            row = await session.get(ApiAccessKeyRow, key_id)
            if row is not None:
                row.last_used_at = used_at
                await session.commit()
