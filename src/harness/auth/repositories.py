"""Persistence adapters for authentication and audit state."""

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol, cast

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.exc import IntegrityError

from harness.auth.models import AuditEntry, AuthUser, Membership, RefreshToken, Role
from harness.core.errors import ConflictError, NotFoundError
from harness.storage.database import SessionFactory
from harness.storage.models import (
    AuditLogRow,
    OAuthIdentityRow,
    RefreshTokenRow,
    TenantMembershipRow,
    UserRow,
)


class AuthRepository(Protocol):
    async def create_user(
        self, user: AuthUser, membership: Membership
    ) -> tuple[AuthUser, Membership]: ...

    async def get_user_by_email(self, email: str) -> AuthUser | None: ...

    async def get_user(self, user_id: str) -> AuthUser: ...

    async def save_user(self, user: AuthUser) -> AuthUser: ...

    async def get_membership(self, tenant_id: str, user_id: str) -> Membership: ...

    async def list_members(self, tenant_id: str) -> list[tuple[AuthUser, Membership]]: ...

    async def save_membership(self, membership: Membership) -> Membership: ...

    async def count_members(self, tenant_id: str) -> int: ...

    async def count_members_with_role(self, tenant_id: str, role: Role) -> int: ...

    async def get_oauth_user(self, provider: str, subject: str) -> AuthUser | None: ...

    async def link_oauth_identity(
        self,
        *,
        identity_id: str,
        provider: str,
        subject: str,
        user_id: str,
        provider_email: str,
        created_at: object,
    ) -> None: ...

    async def add_refresh_token(self, token: RefreshToken) -> None: ...

    async def replace_user_refresh_token(self, token: RefreshToken, revoked_at: object) -> None: ...

    async def get_refresh_token(self, token_hash: str) -> RefreshToken | None: ...

    async def rotate_refresh_token(
        self, current_hash: str, replacement: RefreshToken, revoked_at: object
    ) -> bool: ...

    async def revoke_token_family(self, family_id: str, revoked_at: object) -> None: ...

    async def revoke_user_tokens(self, user_id: str, revoked_at: object) -> None: ...

    async def is_token_family_active(
        self, user_id: str, tenant_id: str, family_id: str, now: object
    ) -> bool: ...


class AuditRepository(Protocol):
    async def add(self, entry: AuditEntry) -> None: ...

    async def list_for_tenant(self, tenant_id: str, *, limit: int) -> list[AuditEntry]: ...


class InMemoryAuthRepository:
    def __init__(self) -> None:
        self.users: dict[str, AuthUser] = {}
        self.users_by_email: dict[str, str] = {}
        self.memberships: dict[tuple[str, str], Membership] = {}
        self.identities: dict[tuple[str, str], str] = {}
        self.refresh_tokens: dict[str, RefreshToken] = {}

    async def create_user(
        self, user: AuthUser, membership: Membership
    ) -> tuple[AuthUser, Membership]:
        if user.email in self.users_by_email:
            raise ConflictError("an account with this email already exists")
        self.users[user.user_id] = user
        self.users_by_email[user.email] = user.user_id
        self.memberships[(membership.tenant_id, membership.user_id)] = membership
        return user, membership

    async def get_user_by_email(self, email: str) -> AuthUser | None:
        user_id = self.users_by_email.get(email)
        return None if user_id is None else self.users[user_id]

    async def get_user(self, user_id: str) -> AuthUser:
        try:
            return self.users[user_id]
        except KeyError as error:
            raise NotFoundError("user not found") from error

    async def save_user(self, user: AuthUser) -> AuthUser:
        if user.user_id not in self.users:
            raise NotFoundError("user not found")
        self.users[user.user_id] = user
        self.users_by_email[user.email] = user.user_id
        return user

    async def get_membership(self, tenant_id: str, user_id: str) -> Membership:
        try:
            return self.memberships[(tenant_id, user_id)]
        except KeyError as error:
            raise NotFoundError("tenant membership not found") from error

    async def list_members(self, tenant_id: str) -> list[tuple[AuthUser, Membership]]:
        memberships = sorted(
            (
                membership
                for (membership_tenant_id, _), membership in self.memberships.items()
                if membership_tenant_id == tenant_id
            ),
            key=lambda item: item.created_at,
        )
        return [(self.users[item.user_id], item) for item in memberships]

    async def save_membership(self, membership: Membership) -> Membership:
        key = (membership.tenant_id, membership.user_id)
        if key not in self.memberships:
            raise NotFoundError("tenant membership not found")
        self.memberships[key] = membership
        return membership

    async def count_members(self, tenant_id: str) -> int:
        return sum(key[0] == tenant_id for key in self.memberships)

    async def count_members_with_role(self, tenant_id: str, role: Role) -> int:
        return sum(
            membership.tenant_id == tenant_id and membership.role == role
            for membership in self.memberships.values()
        )

    async def get_oauth_user(self, provider: str, subject: str) -> AuthUser | None:
        user_id = self.identities.get((provider, subject))
        return None if user_id is None else self.users[user_id]

    async def link_oauth_identity(
        self,
        *,
        identity_id: str,
        provider: str,
        subject: str,
        user_id: str,
        provider_email: str,
        created_at: object,
    ) -> None:
        del identity_id, provider_email, created_at
        existing = self.identities.get((provider, subject))
        if existing is not None and existing != user_id:
            raise ConflictError("OAuth identity is already linked")
        self.identities[(provider, subject)] = user_id

    async def add_refresh_token(self, token: RefreshToken) -> None:
        self.refresh_tokens[token.token_hash] = token

    async def replace_user_refresh_token(self, token: RefreshToken, revoked_at: object) -> None:
        await self.revoke_user_tokens(token.user_id, revoked_at)
        self.refresh_tokens[token.token_hash] = token

    async def get_refresh_token(self, token_hash: str) -> RefreshToken | None:
        return self.refresh_tokens.get(token_hash)

    async def rotate_refresh_token(
        self, current_hash: str, replacement: RefreshToken, revoked_at: object
    ) -> bool:
        current = self.refresh_tokens.get(current_hash)
        if current is None or current.revoked_at is not None:
            return False
        self.refresh_tokens[current_hash] = current.model_copy(
            update={
                "revoked_at": revoked_at,
                "replaced_by_hash": replacement.token_hash,
            }
        )
        for token_hash, token in tuple(self.refresh_tokens.items()):
            if (
                token.user_id == replacement.user_id
                and token.family_id != replacement.family_id
                and token.revoked_at is None
            ):
                self.refresh_tokens[token_hash] = token.model_copy(
                    update={"revoked_at": revoked_at}
                )
        self.refresh_tokens[replacement.token_hash] = replacement
        return True

    async def revoke_token_family(self, family_id: str, revoked_at: object) -> None:
        for token_hash, token in tuple(self.refresh_tokens.items()):
            if token.family_id == family_id and token.revoked_at is None:
                self.refresh_tokens[token_hash] = token.model_copy(
                    update={"revoked_at": revoked_at}
                )

    async def revoke_user_tokens(self, user_id: str, revoked_at: object) -> None:
        for token_hash, token in tuple(self.refresh_tokens.items()):
            if token.user_id == user_id and token.revoked_at is None:
                self.refresh_tokens[token_hash] = token.model_copy(
                    update={"revoked_at": revoked_at}
                )

    async def is_token_family_active(
        self, user_id: str, tenant_id: str, family_id: str, now: object
    ) -> bool:
        return any(
            token.user_id == user_id
            and token.tenant_id == tenant_id
            and token.family_id == family_id
            and token.revoked_at is None
            and token.expires_at > cast(datetime, now)
            for token in self.refresh_tokens.values()
        )


class InMemoryAuditRepository:
    def __init__(self) -> None:
        self.entries: list[AuditEntry] = []

    async def add(self, entry: AuditEntry) -> None:
        self.entries.append(entry)

    async def list_for_tenant(self, tenant_id: str, *, limit: int) -> list[AuditEntry]:
        matches = [entry for entry in self.entries if entry.tenant_id == tenant_id]
        return sorted(matches, key=lambda item: item.occurred_at, reverse=True)[:limit]


class PostgresAuthRepository:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def create_user(
        self, user: AuthUser, membership: Membership
    ) -> tuple[AuthUser, Membership]:
        async with self._sessions() as session:
            session.add(
                UserRow(
                    user_id=user.user_id,
                    email=user.email,
                    display_name=user.display_name,
                    password_hash=user.password_hash,
                    email_verified=user.email_verified,
                    disabled=user.disabled,
                    created_at=user.created_at,
                    updated_at=user.updated_at,
                )
            )
            session.add(
                TenantMembershipRow(
                    tenant_id=membership.tenant_id,
                    user_id=membership.user_id,
                    role=membership.role,
                    created_at=membership.created_at,
                )
            )
            try:
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                raise ConflictError("an account with this email already exists") from error
        return user, membership

    async def get_user_by_email(self, email: str) -> AuthUser | None:
        async with self._sessions() as session:
            row = (
                await session.execute(select(UserRow).where(UserRow.email == email))
            ).scalar_one_or_none()
            return None if row is None else _user_from_row(row)

    async def get_user(self, user_id: str) -> AuthUser:
        async with self._sessions() as session:
            row = await session.get(UserRow, user_id)
            if row is None:
                raise NotFoundError("user not found")
            return _user_from_row(row)

    async def save_user(self, user: AuthUser) -> AuthUser:
        async with self._sessions() as session:
            result = await session.execute(
                update(UserRow)
                .where(UserRow.user_id == user.user_id)
                .values(
                    display_name=user.display_name,
                    password_hash=user.password_hash,
                    email_verified=user.email_verified,
                    disabled=user.disabled,
                    updated_at=user.updated_at,
                )
            )
            if not cast(CursorResult[object], result).rowcount:
                await session.rollback()
                raise NotFoundError("user not found")
            await session.commit()
        return user

    async def get_membership(self, tenant_id: str, user_id: str) -> Membership:
        async with self._sessions() as session:
            row = await session.get(TenantMembershipRow, (tenant_id, user_id))
            if row is None:
                raise NotFoundError("tenant membership not found")
            return _membership_from_row(row)

    async def list_members(self, tenant_id: str) -> list[tuple[AuthUser, Membership]]:
        statement = (
            select(UserRow, TenantMembershipRow)
            .join(
                TenantMembershipRow,
                TenantMembershipRow.user_id == UserRow.user_id,
            )
            .where(TenantMembershipRow.tenant_id == tenant_id)
            .order_by(TenantMembershipRow.created_at.asc())
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
            return [
                (_user_from_row(user), _membership_from_row(membership))
                for user, membership in rows
            ]

    async def save_membership(self, membership: Membership) -> Membership:
        async with self._sessions() as session:
            result = await session.execute(
                update(TenantMembershipRow)
                .where(
                    TenantMembershipRow.tenant_id == membership.tenant_id,
                    TenantMembershipRow.user_id == membership.user_id,
                )
                .values(role=membership.role)
            )
            if not cast(CursorResult[object], result).rowcount:
                await session.rollback()
                raise NotFoundError("tenant membership not found")
            await session.commit()
        return membership

    async def count_members(self, tenant_id: str) -> int:
        statement = (
            select(func.count())
            .select_from(TenantMembershipRow)
            .where(TenantMembershipRow.tenant_id == tenant_id)
        )
        async with self._sessions() as session:
            return int((await session.execute(statement)).scalar_one())

    async def count_members_with_role(self, tenant_id: str, role: Role) -> int:
        statement = (
            select(func.count())
            .select_from(TenantMembershipRow)
            .where(
                TenantMembershipRow.tenant_id == tenant_id,
                TenantMembershipRow.role == role,
            )
        )
        async with self._sessions() as session:
            return int((await session.execute(statement)).scalar_one())

    async def get_oauth_user(self, provider: str, subject: str) -> AuthUser | None:
        statement = (
            select(UserRow)
            .join(OAuthIdentityRow, OAuthIdentityRow.user_id == UserRow.user_id)
            .where(
                OAuthIdentityRow.provider == provider,
                OAuthIdentityRow.subject == subject,
            )
        )
        async with self._sessions() as session:
            row = (await session.execute(statement)).scalar_one_or_none()
            return None if row is None else _user_from_row(row)

    async def link_oauth_identity(
        self,
        *,
        identity_id: str,
        provider: str,
        subject: str,
        user_id: str,
        provider_email: str,
        created_at: object,
    ) -> None:
        from datetime import datetime

        async with self._sessions() as session:
            session.add(
                OAuthIdentityRow(
                    identity_id=identity_id,
                    provider=provider,
                    subject=subject,
                    user_id=user_id,
                    provider_email=provider_email,
                    created_at=cast(datetime, created_at),
                )
            )
            try:
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                raise ConflictError("OAuth identity is already linked") from error

    async def add_refresh_token(self, token: RefreshToken) -> None:
        async with self._sessions() as session:
            session.add(_refresh_row(token))
            await session.commit()

    async def replace_user_refresh_token(self, token: RefreshToken, revoked_at: object) -> None:
        from datetime import datetime

        async with self._sessions() as session:
            # Serialize competing logins for one user so the last completed
            # login is the only active browser/device session.
            await session.execute(
                select(UserRow.user_id)
                .where(UserRow.user_id == token.user_id)
                .with_for_update()
            )
            await session.execute(
                update(RefreshTokenRow)
                .where(
                    RefreshTokenRow.user_id == token.user_id,
                    RefreshTokenRow.revoked_at.is_(None),
                )
                .values(revoked_at=cast(datetime, revoked_at))
            )
            session.add(_refresh_row(token))
            await session.commit()

    async def get_refresh_token(self, token_hash: str) -> RefreshToken | None:
        async with self._sessions() as session:
            row = await session.get(RefreshTokenRow, token_hash)
            return None if row is None else _refresh_from_row(row)

    async def rotate_refresh_token(
        self, current_hash: str, replacement: RefreshToken, revoked_at: object
    ) -> bool:
        from datetime import datetime

        async with self._sessions() as session:
            await session.execute(
                select(UserRow.user_id)
                .where(UserRow.user_id == replacement.user_id)
                .with_for_update()
            )
            result = await session.execute(
                update(RefreshTokenRow)
                .where(
                    RefreshTokenRow.token_hash == current_hash,
                    RefreshTokenRow.revoked_at.is_(None),
                )
                .values(
                    revoked_at=cast(datetime, revoked_at),
                    replaced_by_hash=replacement.token_hash,
                )
            )
            if not cast(CursorResult[object], result).rowcount:
                await session.rollback()
                return False
            await session.execute(
                update(RefreshTokenRow)
                .where(
                    RefreshTokenRow.user_id == replacement.user_id,
                    RefreshTokenRow.family_id != replacement.family_id,
                    RefreshTokenRow.revoked_at.is_(None),
                )
                .values(revoked_at=cast(datetime, revoked_at))
            )
            session.add(_refresh_row(replacement))
            await session.commit()
            return True

    async def revoke_token_family(self, family_id: str, revoked_at: object) -> None:
        from datetime import datetime

        async with self._sessions() as session:
            await session.execute(
                update(RefreshTokenRow)
                .where(
                    RefreshTokenRow.family_id == family_id,
                    RefreshTokenRow.revoked_at.is_(None),
                )
                .values(revoked_at=cast(datetime, revoked_at))
            )
            await session.commit()

    async def revoke_user_tokens(self, user_id: str, revoked_at: object) -> None:
        from datetime import datetime

        async with self._sessions() as session:
            await session.execute(
                update(RefreshTokenRow)
                .where(
                    RefreshTokenRow.user_id == user_id,
                    RefreshTokenRow.revoked_at.is_(None),
                )
                .values(revoked_at=cast(datetime, revoked_at))
            )
            await session.commit()

    async def is_token_family_active(
        self, user_id: str, tenant_id: str, family_id: str, now: object
    ) -> bool:
        from datetime import datetime

        statement = (
            select(func.count())
            .select_from(RefreshTokenRow)
            .where(
                RefreshTokenRow.user_id == user_id,
                RefreshTokenRow.tenant_id == tenant_id,
                RefreshTokenRow.family_id == family_id,
                RefreshTokenRow.revoked_at.is_(None),
                RefreshTokenRow.expires_at > cast(datetime, now),
            )
        )
        async with self._sessions() as session:
            return int((await session.execute(statement)).scalar_one()) > 0


class PostgresAuditRepository:
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def add(self, entry: AuditEntry) -> None:
        async with self._sessions() as session:
            session.add(
                AuditLogRow(
                    audit_id=entry.audit_id,
                    occurred_at=entry.occurred_at,
                    tenant_id=entry.tenant_id,
                    user_id=entry.user_id,
                    action=entry.action,
                    resource_type=entry.resource_type,
                    resource_id=entry.resource_id,
                    outcome=entry.outcome,
                    ip_address=entry.ip_address,
                    user_agent=entry.user_agent,
                    details=entry.details,
                )
            )
            await session.commit()

    async def list_for_tenant(self, tenant_id: str, *, limit: int) -> list[AuditEntry]:
        statement = (
            select(AuditLogRow)
            .where(AuditLogRow.tenant_id == tenant_id)
            .order_by(AuditLogRow.occurred_at.desc())
            .limit(limit)
        )
        async with self._sessions() as session:
            rows: Sequence[AuditLogRow] = (await session.execute(statement)).scalars().all()
            return [_audit_from_row(row) for row in rows]


def _user_from_row(row: UserRow) -> AuthUser:
    return AuthUser(
        user_id=row.user_id,
        email=row.email,
        display_name=row.display_name,
        password_hash=row.password_hash,
        email_verified=row.email_verified,
        disabled=row.disabled,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _membership_from_row(row: TenantMembershipRow) -> Membership:
    return Membership(
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        role=cast(Role, row.role),
        created_at=row.created_at,
    )


def _refresh_row(token: RefreshToken) -> RefreshTokenRow:
    return RefreshTokenRow(**token.model_dump())


def _refresh_from_row(row: RefreshTokenRow) -> RefreshToken:
    return RefreshToken(
        token_hash=row.token_hash,
        family_id=row.family_id,
        user_id=row.user_id,
        tenant_id=row.tenant_id,
        expires_at=row.expires_at,
        created_at=row.created_at,
        revoked_at=row.revoked_at,
        replaced_by_hash=row.replaced_by_hash,
    )


def _audit_from_row(row: AuditLogRow) -> AuditEntry:
    return AuditEntry(
        audit_id=row.audit_id,
        occurred_at=row.occurred_at,
        tenant_id=row.tenant_id,
        user_id=row.user_id,
        action=row.action,
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        outcome=row.outcome,
        ip_address=row.ip_address,
        user_agent=row.user_agent,
        details=row.details,
    )
