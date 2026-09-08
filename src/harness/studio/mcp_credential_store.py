"""Encrypted, tenant-scoped MCP credentials configured from Agent Studio."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Protocol, cast

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import Field, SecretStr

from harness.auth.audit import AuditService
from harness.core.models import ExecutionIdentity
from harness.runtime.mcp_credentials import (
    DynamicMcpCredentialProvider,
    McpCredentialError,
)
from harness.studio.models import StudioModel


@dataclass(frozen=True)
class StoredMcpCredential:
    tenant_id: str
    owner_user_id: str
    reference: str
    revision: int
    key_names: tuple[str, ...]
    ciphertext: str
    updated_by: str
    updated_at: datetime


class McpCredentialRepository(Protocol):
    async def get(
        self, tenant_id: str, owner_user_id: str, reference: str
    ) -> StoredMcpCredential | None: ...

    async def list_for_user(
        self, tenant_id: str, owner_user_id: str
    ) -> tuple[StoredMcpCredential, ...]: ...

    async def upsert(self, value: StoredMcpCredential) -> StoredMcpCredential: ...

    async def delete(self, tenant_id: str, owner_user_id: str, reference: str) -> bool: ...


class InMemoryMcpCredentialRepository:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str, str], StoredMcpCredential] = {}
        self._lock = asyncio.Lock()

    async def get(
        self, tenant_id: str, owner_user_id: str, reference: str
    ) -> StoredMcpCredential | None:
        return self._items.get((tenant_id, owner_user_id, reference))

    async def list_for_user(
        self, tenant_id: str, owner_user_id: str
    ) -> tuple[StoredMcpCredential, ...]:
        return tuple(
            sorted(
                (
                    value
                    for (scope, owner, _), value in self._items.items()
                    if scope == tenant_id and owner == owner_user_id
                ),
                key=lambda value: value.reference,
            )
        )

    async def upsert(self, value: StoredMcpCredential) -> StoredMcpCredential:
        async with self._lock:
            current = self._items.get((value.tenant_id, value.owner_user_id, value.reference))
            stored = replace(
                value,
                revision=(current.revision + 1 if current is not None else 1),
            )
            self._items[(stored.tenant_id, stored.owner_user_id, stored.reference)] = stored
            return stored

    async def delete(self, tenant_id: str, owner_user_id: str, reference: str) -> bool:
        async with self._lock:
            return self._items.pop((tenant_id, owner_user_id, reference), None) is not None


class McpCredentialCipher:
    def __init__(self, secret: SecretStr) -> None:
        material = secret.get_secret_value().encode("utf-8")
        if not material:
            raise ValueError("MCP credential encryption secret must not be empty")
        self._cipher = AESGCM(hashlib.sha256(b"harness-mcp-v1\0" + material).digest())

    @staticmethod
    def _aad(tenant_id: str, owner_user_id: str, reference: str) -> bytes:
        return f"{tenant_id}\0{owner_user_id}\0{reference}".encode()

    def encrypt(
        self,
        tenant_id: str,
        owner_user_id: str,
        reference: str,
        values: Mapping[str, SecretStr],
    ) -> str:
        nonce = os.urandom(12)
        payload = json.dumps(
            {key: value.get_secret_value() for key, value in values.items()},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        sealed = self._cipher.encrypt(
            nonce, payload, self._aad(tenant_id, owner_user_id, reference)
        )
        return base64.urlsafe_b64encode(nonce + sealed).decode("ascii")

    def decrypt(self, value: StoredMcpCredential) -> Mapping[str, SecretStr]:
        packed = base64.urlsafe_b64decode(value.ciphertext.encode("ascii"))
        try:
            payload = self._cipher.decrypt(
                packed[:12],
                packed[12:],
                self._aad(value.tenant_id, value.owner_user_id, value.reference),
            )
        except InvalidTag:
            # Credentials written before user isolation used tenant + reference
            # as AAD. The migration preserves ciphertext and assigns its creator.
            payload = self._cipher.decrypt(
                packed[:12],
                packed[12:],
                f"{value.tenant_id}\0{value.reference}".encode(),
            )
        raw = cast(object, json.loads(payload))
        if not isinstance(raw, dict):
            raise ValueError("Stored MCP credential payload is invalid")
        values = cast(dict[object, object], raw)
        if any(
            not isinstance(key, str) or not isinstance(secret, str)
            for key, secret in values.items()
        ):
            raise ValueError("Stored MCP credential payload is invalid")
        decoded = cast(dict[str, str], values)
        return MappingProxyType({key: SecretStr(secret) for key, secret in decoded.items()})


class ConfigureMcpCredentialRequest(StudioModel):
    auth_key: str = Field(alias="authKey", pattern=r"^[a-z][a-z0-9_]*$")
    value: SecretStr = Field(min_length=1, max_length=16_384)


class McpCredentialStatus(StudioModel):
    reference: str
    configured: bool
    key_names: tuple[str, ...] = Field(default=(), alias="keyNames")
    revision: int | None = None
    updated_by: str | None = Field(default=None, alias="updatedBy")
    updated_at: datetime | None = Field(default=None, alias="updatedAt")


class McpCredentialService:
    def __init__(
        self,
        repository: McpCredentialRepository,
        cipher: McpCredentialCipher,
        *,
        audit: AuditService | None = None,
    ) -> None:
        self.repository = repository
        self.cipher = cipher
        self.audit = audit

    @staticmethod
    def _status(value: StoredMcpCredential) -> McpCredentialStatus:
        return McpCredentialStatus(
            reference=value.reference,
            configured=True,
            keyNames=value.key_names,
            revision=value.revision,
            updatedBy=value.updated_by,
            updatedAt=value.updated_at,
        )

    async def list(self, tenant_id: str, owner_user_id: str) -> tuple[McpCredentialStatus, ...]:
        items = await self.repository.list_for_user(tenant_id, owner_user_id)
        return tuple(self._status(item) for item in items)

    async def is_configured(self, tenant_id: str, owner_user_id: str, reference: str) -> bool:
        return await self.repository.get(tenant_id, owner_user_id, reference) is not None

    async def configure(
        self,
        tenant_id: str,
        user_id: str,
        reference: str,
        request: ConfigureMcpCredentialRequest,
    ) -> McpCredentialStatus:
        now = datetime.now(UTC)
        stored = await self.repository.upsert(
            StoredMcpCredential(
                tenant_id=tenant_id,
                owner_user_id=user_id,
                reference=reference,
                revision=1,
                key_names=(request.auth_key,),
                ciphertext=self.cipher.encrypt(
                    tenant_id,
                    user_id,
                    reference,
                    {request.auth_key: request.value},
                ),
                updated_by=user_id,
                updated_at=now,
            )
        )
        if self.audit is not None:
            await self.audit.record(
                tenant_id=tenant_id,
                user_id=user_id,
                action="studio.mcp_credential.configure",
                resource_type="mcp_credential",
                resource_id=reference,
                details={"keys": list(stored.key_names), "revision": stored.revision},
            )
        return self._status(stored)

    async def delete(self, tenant_id: str, user_id: str, reference: str) -> bool:
        deleted = await self.repository.delete(tenant_id, user_id, reference)
        if deleted and self.audit is not None:
            await self.audit.record(
                tenant_id=tenant_id,
                user_id=user_id,
                action="studio.mcp_credential.delete",
                resource_type="mcp_credential",
                resource_id=reference,
            )
        return deleted


class StoredMcpCredentialProvider:
    def __init__(
        self,
        service: McpCredentialService,
        fallback: DynamicMcpCredentialProvider,
    ) -> None:
        self._service = service
        self._fallback = fallback

    async def resolve(
        self,
        server_reference: str,
        identity: ExecutionIdentity,
        required_keys: frozenset[str],
    ) -> Mapping[str, SecretStr]:
        if identity.connection_mode == "service_owned" and identity.team_ids:
            # Workspace-provided credentials: resolve by the pinned space.
            # Caller-owned personal credentials are never leaked into a
            # service-owned shared run.
            stored = await self._service.repository.get(
                identity.tenant_id,
                space_credential_owner(identity.team_ids[0]),
                server_reference,
            )
            if stored is not None:
                values = self._service.cipher.decrypt(stored)
                if required_keys.issubset(values):
                    return MappingProxyType({key: values[key] for key in required_keys})
        else:
            stored = await self._service.repository.get(
                identity.tenant_id,
                identity.user_id,
                server_reference,
            )
            if stored is not None:
                values = self._service.cipher.decrypt(stored)
                if required_keys.issubset(values):
                    return MappingProxyType({key: values[key] for key in required_keys})
        try:
            return await self._fallback.resolve(server_reference, identity, required_keys)
        except McpCredentialError:
            names = ", ".join(f"{server_reference}.{key}" for key in sorted(required_keys))
            raise McpCredentialError(f"missing MCP credentials: {names}") from None


def space_credential_owner(space_id: str) -> str:
    """Owner identity of space-scoped MCP credentials in the shared store."""
    return f"space:{space_id}"
