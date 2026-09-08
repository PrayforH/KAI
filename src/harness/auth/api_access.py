"""Scoped API keys for external integrations.

Only a SHA-256 digest is persisted. The plaintext token is returned exactly
once at creation time and cannot be recovered by the server afterwards.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from hmac import compare_digest
from secrets import token_urlsafe
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from harness.auth.audit import AuditService
from harness.core.errors import ConflictError, NotFoundError

API_KEY_PERMISSIONS = frozenset(
    {
        "tasks:read",
        "tasks:write",
        "studio:read",
    }
)


class ApiAccessKey(BaseModel):
    model_config = ConfigDict(frozen=True)

    key_id: str
    tenant_id: str
    user_id: str
    name: str
    prefix: str
    token_hash: str = Field(exclude=True)
    permissions: tuple[str, ...]
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class ApiAccessKeyRepository(Protocol):
    async def create(self, value: ApiAccessKey) -> ApiAccessKey: ...

    async def get_by_hash(self, token_hash: str) -> ApiAccessKey | None: ...

    async def list_for_tenant(self, tenant_id: str) -> tuple[ApiAccessKey, ...]: ...

    async def revoke(
        self, tenant_id: str, key_id: str, revoked_at: datetime
    ) -> ApiAccessKey | None: ...

    async def touch(self, key_id: str, used_at: datetime) -> None: ...


class InMemoryApiAccessKeyRepository:
    def __init__(self) -> None:
        self._values: dict[str, ApiAccessKey] = {}

    async def create(self, value: ApiAccessKey) -> ApiAccessKey:
        self._values[value.key_id] = value
        return value

    async def get_by_hash(self, token_hash: str) -> ApiAccessKey | None:
        return next(
            (value for value in self._values.values() if value.token_hash == token_hash),
            None,
        )

    async def list_for_tenant(self, tenant_id: str) -> tuple[ApiAccessKey, ...]:
        return tuple(
            sorted(
                (value for value in self._values.values() if value.tenant_id == tenant_id),
                key=lambda item: item.created_at,
                reverse=True,
            )
        )

    async def revoke(
        self, tenant_id: str, key_id: str, revoked_at: datetime
    ) -> ApiAccessKey | None:
        value = self._values.get(key_id)
        if value is None or value.tenant_id != tenant_id:
            return None
        updated = value.model_copy(update={"revoked_at": revoked_at})
        self._values[key_id] = updated
        return updated

    async def touch(self, key_id: str, used_at: datetime) -> None:
        value = self._values.get(key_id)
        if value is not None:
            self._values[key_id] = value.model_copy(update={"last_used_at": used_at})


class ApiAccessService:
    def __init__(
        self,
        repository: ApiAccessKeyRepository,
        *,
        audit: AuditService | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        id_generator: Callable[[], str] = lambda: token_urlsafe(12),
    ) -> None:
        self.repository = repository
        self.audit = audit
        self._clock = clock
        self._id_generator = id_generator

    async def list(self, tenant_id: str) -> tuple[ApiAccessKey, ...]:
        return await self.repository.list_for_tenant(tenant_id)

    async def create(
        self,
        tenant_id: str,
        user_id: str,
        name: str,
        permissions: tuple[str, ...],
    ) -> tuple[ApiAccessKey, str]:
        normalized = tuple(dict.fromkeys(permissions))
        unsupported = set(normalized) - API_KEY_PERMISSIONS
        if unsupported:
            raise ConflictError(
                "unsupported API key permissions: " + ", ".join(sorted(unsupported))
            )
        if not normalized:
            raise ConflictError("at least one API key permission is required")
        token = f"ask_{token_urlsafe(32)}"
        now = self._clock()
        value = ApiAccessKey(
            key_id=f"key-{self._id_generator()}",
            tenant_id=tenant_id,
            user_id=user_id,
            name=name.strip(),
            prefix=token[:12],
            token_hash=_hash(token),
            permissions=normalized,
            created_at=now,
        )
        await self.repository.create(value)
        if self.audit is not None:
            await self.audit.record(
                tenant_id=tenant_id,
                user_id=user_id,
                action="auth.api_key.create",
                resource_type="api_key",
                resource_id=value.key_id,
                details={"permissions": list(normalized)},
            )
        return value, token

    async def revoke(self, tenant_id: str, user_id: str, key_id: str) -> ApiAccessKey:
        value = await self.repository.revoke(tenant_id, key_id, self._clock())
        if value is None:
            raise NotFoundError(f"API key not found: {key_id}")
        if self.audit is not None:
            await self.audit.record(
                tenant_id=tenant_id,
                user_id=user_id,
                action="auth.api_key.revoke",
                resource_type="api_key",
                resource_id=key_id,
                details={},
            )
        return value

    async def authenticate(self, token: str) -> ApiAccessKey | None:
        if not token.startswith("ask_") or len(token) < 24:
            return None
        expected_hash = _hash(token)
        value = await self.repository.get_by_hash(expected_hash)
        if (
            value is None
            or value.revoked_at is not None
            or not compare_digest(value.token_hash, expected_hash)
        ):
            return None
        now = self._clock()
        await self.repository.touch(value.key_id, now)
        return value.model_copy(update={"last_used_at": now})


def _hash(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()
