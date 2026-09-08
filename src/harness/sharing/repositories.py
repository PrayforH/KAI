"""Persistence ports and in-memory implementation for team spaces."""

import asyncio
from typing import Protocol

from harness.core.errors import ConflictError, NotFoundError
from harness.sharing.models import (
    SharedKnowledgeBase,
    TeamSpace,
    TeamSpaceMember,
)


class TeamSpaceRepository(Protocol):
    async def add_space(self, space: TeamSpace, owner: TeamSpaceMember) -> None: ...
    async def get_space(self, tenant_id: str, space_id: str) -> TeamSpace: ...
    async def list_spaces_for_user(self, tenant_id: str, user_id: str) -> list[TeamSpace]: ...
    async def get_member(
        self, tenant_id: str, space_id: str, user_id: str
    ) -> TeamSpaceMember | None: ...
    async def list_members(self, tenant_id: str, space_id: str) -> list[TeamSpaceMember]: ...
    async def put_member(self, member: TeamSpaceMember) -> None: ...
    async def delete_member(self, tenant_id: str, space_id: str, user_id: str) -> bool: ...
    async def add_shared_knowledge(self, shared: SharedKnowledgeBase) -> None: ...
    async def list_shared_knowledge(
        self, tenant_id: str, space_id: str
    ) -> list[SharedKnowledgeBase]: ...
    async def delete_shared_knowledge(
        self, tenant_id: str, space_id: str, reference: str
    ) -> bool: ...


class InMemoryTeamSpaceRepository:
    def __init__(self) -> None:
        self._spaces: dict[tuple[str, str], TeamSpace] = {}
        self._members: dict[tuple[str, str, str], TeamSpaceMember] = {}
        self._knowledge: dict[tuple[str, str, str], SharedKnowledgeBase] = {}
        self._lock = asyncio.Lock()

    async def add_space(self, space: TeamSpace, owner: TeamSpaceMember) -> None:
        key = (space.tenant_id, space.space_id)
        async with self._lock:
            if key in self._spaces:
                raise ConflictError(f"team space already exists: {space.space_id}")
            self._spaces[key] = space
            self._members[(space.tenant_id, space.space_id, owner.user_id)] = owner

    async def get_space(self, tenant_id: str, space_id: str) -> TeamSpace:
        try:
            return self._spaces[(tenant_id, space_id)]
        except KeyError as error:
            raise NotFoundError(f"team space not found: {space_id}") from error

    async def list_spaces_for_user(self, tenant_id: str, user_id: str) -> list[TeamSpace]:
        space_ids = {
            space_id
            for stored_tenant, space_id, stored_user in self._members
            if stored_tenant == tenant_id and stored_user == user_id
        }
        return sorted(
            [
                space
                for (stored_tenant, space_id), space in self._spaces.items()
                if stored_tenant == tenant_id and space_id in space_ids
            ],
            key=lambda item: (item.name, item.space_id),
        )

    async def get_member(
        self, tenant_id: str, space_id: str, user_id: str
    ) -> TeamSpaceMember | None:
        return self._members.get((tenant_id, space_id, user_id))

    async def list_members(self, tenant_id: str, space_id: str) -> list[TeamSpaceMember]:
        return sorted(
            [
                member
                for (stored_tenant, stored_space, _), member in self._members.items()
                if stored_tenant == tenant_id and stored_space == space_id
            ],
            key=lambda item: (item.role.value, item.user_id),
        )

    async def put_member(self, member: TeamSpaceMember) -> None:
        self._members[(member.tenant_id, member.space_id, member.user_id)] = member

    async def delete_member(self, tenant_id: str, space_id: str, user_id: str) -> bool:
        return self._members.pop((tenant_id, space_id, user_id), None) is not None

    async def add_shared_knowledge(self, shared: SharedKnowledgeBase) -> None:
        key = (shared.tenant_id, shared.space_id, shared.knowledge_base_reference)
        async with self._lock:
            if key in self._knowledge:
                raise ConflictError(
                    f"knowledge base is already shared: {shared.knowledge_base_reference}"
                )
            self._knowledge[key] = shared

    async def list_shared_knowledge(
        self, tenant_id: str, space_id: str
    ) -> list[SharedKnowledgeBase]:
        return sorted(
            [
                shared
                for (stored_tenant, stored_space, _), shared in self._knowledge.items()
                if stored_tenant == tenant_id and stored_space == space_id
            ],
            key=lambda item: item.knowledge_base_reference,
        )

    async def delete_shared_knowledge(
        self, tenant_id: str, space_id: str, reference: str
    ) -> bool:
        return self._knowledge.pop((tenant_id, space_id, reference), None) is not None
