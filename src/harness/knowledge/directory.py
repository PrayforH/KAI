"""Identity directory seam for knowledge base membership.

Phase 1 resolves members against the platform ``users`` table. Phase 2 swaps in
an IDAAS-backed implementation of the same port for organization-tree grants,
without changing the knowledge base member table or API contract.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select

from harness.storage.database import SessionFactory
from harness.storage.models import UserRow


@dataclass(frozen=True)
class DirectoryUser:
    user_id: str
    email: str
    display_name: str


class IdentityDirectoryPort(Protocol):
    """Resolve and search platform identities for membership grants."""

    async def resolve_users(
        self,
        *,
        user_ids: Sequence[str] = (),
        emails: Sequence[str] = (),
    ) -> tuple[DirectoryUser, ...]:
        """Return known users matching any of the supplied ids or emails."""
        ...

    async def search_users(self, query: str, *, limit: int = 20) -> tuple[DirectoryUser, ...]:
        """Search users by display name or email for the picker UI."""
        ...


class PlatformUserDirectory:
    """Phase-1 implementation backed by the platform ``users`` table."""

    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def resolve_users(
        self,
        *,
        user_ids: Sequence[str] = (),
        emails: Sequence[str] = (),
    ) -> tuple[DirectoryUser, ...]:
        identifiers = {item.strip() for item in user_ids if item.strip()}
        addresses = {item.strip().lower() for item in emails if item.strip()}
        if not identifiers and not addresses:
            return ()
        statement = select(UserRow).where(UserRow.disabled.is_(False))
        if identifiers and addresses:
            statement = statement.where(
                (UserRow.user_id.in_(identifiers)) | (UserRow.email.in_(addresses))
            )
        elif identifiers:
            statement = statement.where(UserRow.user_id.in_(identifiers))
        else:
            statement = statement.where(UserRow.email.in_(addresses))
        async with self._sessions() as db:
            rows = (await db.scalars(statement)).all()
        return tuple(
            DirectoryUser(
                user_id=row.user_id,
                email=row.email,
                display_name=row.display_name,
            )
            for row in rows
        )

    async def search_users(self, query: str, *, limit: int = 20) -> tuple[DirectoryUser, ...]:
        value = query.strip()
        if not value:
            return ()
        pattern = f"%{value.lower()}%"
        statement = (
            select(UserRow)
            .where(UserRow.disabled.is_(False))
            .where(
                (UserRow.email.ilike(pattern)) | (UserRow.display_name.ilike(pattern))
            )
            .order_by(UserRow.email)
            .limit(max(1, min(limit, 50)))
        )
        async with self._sessions() as db:
            rows = (await db.scalars(statement)).all()
        return tuple(
            DirectoryUser(
                user_id=row.user_id,
                email=row.email,
                display_name=row.display_name,
            )
            for row in rows
        )


class InMemoryUserDirectory:
    """Test/dev directory that resolves ids to themselves and emails verbatim."""

    def __init__(self, users: Sequence[DirectoryUser] = ()) -> None:
        self._users = {item.user_id: item for item in users}
        self._by_email = {item.email.lower(): item for item in users}

    def add(self, user: DirectoryUser) -> None:
        self._users[user.user_id] = user
        self._by_email[user.email.lower()] = user

    async def resolve_users(
        self,
        *,
        user_ids: Sequence[str] = (),
        emails: Sequence[str] = (),
    ) -> tuple[DirectoryUser, ...]:
        resolved: list[DirectoryUser] = []
        seen: set[str] = set()
        for identifier in user_ids:
            value = identifier.strip()
            user = self._users.get(value)
            if user and user.user_id not in seen:
                seen.add(user.user_id)
                resolved.append(user)
        for address in emails:
            value = address.strip().lower()
            user = self._by_email.get(value)
            if user and user.user_id not in seen:
                seen.add(user.user_id)
                resolved.append(user)
        return tuple(resolved)

    async def search_users(self, query: str, *, limit: int = 20) -> tuple[DirectoryUser, ...]:
        value = query.strip().lower()
        if not value:
            return ()
        matches = [
            user
            for user in self._users.values()
            if value in user.email.lower() or value in user.display_name.lower()
        ]
        return tuple(matches[: max(1, min(limit, 50))])
