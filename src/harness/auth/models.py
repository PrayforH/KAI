"""Authentication domain models."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Role = Literal["owner", "admin", "member", "viewer"]


class AuthUser(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: str
    email: str
    display_name: str
    password_hash: str | None = Field(default=None, exclude=True)
    email_verified: bool
    disabled: bool
    created_at: datetime
    updated_at: datetime


class Membership(BaseModel):
    model_config = ConfigDict(frozen=True)

    tenant_id: str
    user_id: str
    role: Role
    created_at: datetime


class TenantMember(BaseModel):
    model_config = ConfigDict(frozen=True)

    user: AuthUser
    membership: Membership


class RefreshToken(BaseModel):
    model_config = ConfigDict(frozen=True)

    token_hash: str
    family_id: str
    user_id: str
    tenant_id: str
    expires_at: datetime
    created_at: datetime
    revoked_at: datetime | None = None
    replaced_by_hash: str | None = None


class AuthSession(BaseModel):
    model_config = ConfigDict(frozen=True)

    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: AuthUser
    membership: Membership


class AccessClaims(BaseModel):
    model_config = ConfigDict(frozen=True)

    sub: str
    tenant_id: str
    email: str
    name: str
    roles: tuple[Role, ...]
    iss: str
    aud: str
    token_type: Literal["access"]
    iat: int
    exp: int
    jti: str
    sid: str


class AuditEntry(BaseModel):
    model_config = ConfigDict(frozen=True)

    audit_id: str
    occurred_at: datetime
    tenant_id: str | None
    user_id: str | None
    action: str
    resource_type: str
    resource_id: str | None
    outcome: str
    ip_address: str | None = None
    user_agent: str | None = None
    details: dict[str, object] = {}
