"""PostgreSQL team-space persistence."""

from typing import Any, cast

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.exc import IntegrityError

from harness.core.errors import ConflictError, NotFoundError
from harness.sharing.models import (
    SharedKnowledgeBase,
    TeamSpace,
    TeamSpaceMember,
)
from harness.sharing.repositories import TeamSpaceRepository
from harness.storage.database import SessionFactory
from harness.storage.models import (
    SharedKnowledgeBaseRow,
    TeamSpaceMemberRow,
    TeamSpaceRow,
)


class PostgresTeamSpaceRepository(TeamSpaceRepository):
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def add_space(self, space: TeamSpace, owner: TeamSpaceMember) -> None:
        async with self._sessions() as session:
            session.add_all(
                [
                    TeamSpaceRow(
                        tenant_id=space.tenant_id,
                        space_id=space.space_id,
                        name=space.name,
                        created_at=space.created_at,
                        payload=space.model_dump(mode="json", by_alias=True),
                    ),
                    TeamSpaceMemberRow(
                        tenant_id=owner.tenant_id,
                        space_id=owner.space_id,
                        user_id=owner.user_id,
                        role=owner.role.value,
                        created_at=owner.created_at,
                        payload=owner.model_dump(mode="json", by_alias=True),
                    ),
                ]
            )
            try:
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                raise ConflictError(f"team space already exists: {space.space_id}") from error

    async def get_space(self, tenant_id: str, space_id: str) -> TeamSpace:
        async with self._sessions() as session:
            row = await session.get(TeamSpaceRow, (tenant_id, space_id))
            if row is None:
                raise NotFoundError(f"team space not found: {space_id}")
            return TeamSpace.model_validate(row.payload)

    async def list_spaces_for_user(self, tenant_id: str, user_id: str) -> list[TeamSpace]:
        statement = (
            select(TeamSpaceRow.payload)
            .join(
                TeamSpaceMemberRow,
                (TeamSpaceMemberRow.tenant_id == TeamSpaceRow.tenant_id)
                & (TeamSpaceMemberRow.space_id == TeamSpaceRow.space_id),
            )
            .where(
                TeamSpaceRow.tenant_id == tenant_id,
                TeamSpaceMemberRow.user_id == user_id,
            )
            .order_by(TeamSpaceRow.name, TeamSpaceRow.space_id)
        )
        async with self._sessions() as session:
            return [
                TeamSpace.model_validate(payload)
                for payload in (await session.scalars(statement)).all()
            ]

    async def get_member(
        self, tenant_id: str, space_id: str, user_id: str
    ) -> TeamSpaceMember | None:
        async with self._sessions() as session:
            row = await session.get(TeamSpaceMemberRow, (tenant_id, space_id, user_id))
            return None if row is None else TeamSpaceMember.model_validate(row.payload)

    async def list_members(self, tenant_id: str, space_id: str) -> list[TeamSpaceMember]:
        statement = (
            select(TeamSpaceMemberRow.payload)
            .where(
                TeamSpaceMemberRow.tenant_id == tenant_id,
                TeamSpaceMemberRow.space_id == space_id,
            )
            .order_by(TeamSpaceMemberRow.role, TeamSpaceMemberRow.user_id)
        )
        async with self._sessions() as session:
            return [
                TeamSpaceMember.model_validate(payload)
                for payload in (await session.scalars(statement)).all()
            ]

    async def put_member(self, member: TeamSpaceMember) -> None:
        async with self._sessions() as session:
            row = await session.get(
                TeamSpaceMemberRow,
                (member.tenant_id, member.space_id, member.user_id),
                with_for_update=True,
            )
            if row is None:
                session.add(
                    TeamSpaceMemberRow(
                        tenant_id=member.tenant_id,
                        space_id=member.space_id,
                        user_id=member.user_id,
                        role=member.role.value,
                        created_at=member.created_at,
                        payload=member.model_dump(mode="json", by_alias=True),
                    )
                )
            else:
                row.role = member.role.value
                row.payload = member.model_dump(mode="json", by_alias=True)
            await session.commit()

    async def delete_member(self, tenant_id: str, space_id: str, user_id: str) -> bool:
        async with self._sessions() as session:
            result = await session.execute(
                delete(TeamSpaceMemberRow).where(
                    TeamSpaceMemberRow.tenant_id == tenant_id,
                    TeamSpaceMemberRow.space_id == space_id,
                    TeamSpaceMemberRow.user_id == user_id,
                )
            )
            await session.commit()
            return bool(cast(CursorResult[Any], result).rowcount)

    async def add_shared_knowledge(self, shared: SharedKnowledgeBase) -> None:
        async with self._sessions() as session:
            session.add(
                SharedKnowledgeBaseRow(
                    tenant_id=shared.tenant_id,
                    space_id=shared.space_id,
                    knowledge_base_reference=shared.knowledge_base_reference,
                    created_at=shared.created_at,
                    payload=shared.model_dump(mode="json", by_alias=True),
                )
            )
            try:
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                raise ConflictError(
                    f"knowledge base is already shared: {shared.knowledge_base_reference}"
                ) from error

    async def list_shared_knowledge(
        self, tenant_id: str, space_id: str
    ) -> list[SharedKnowledgeBase]:
        statement = (
            select(SharedKnowledgeBaseRow.payload)
            .where(
                SharedKnowledgeBaseRow.tenant_id == tenant_id,
                SharedKnowledgeBaseRow.space_id == space_id,
            )
            .order_by(SharedKnowledgeBaseRow.knowledge_base_reference)
        )
        async with self._sessions() as session:
            return [
                SharedKnowledgeBase.model_validate(payload)
                for payload in (await session.scalars(statement)).all()
            ]

    async def delete_shared_knowledge(
        self, tenant_id: str, space_id: str, reference: str
    ) -> bool:
        async with self._sessions() as session:
            result = await session.execute(
                delete(SharedKnowledgeBaseRow).where(
                    SharedKnowledgeBaseRow.tenant_id == tenant_id,
                    SharedKnowledgeBaseRow.space_id == space_id,
                    SharedKnowledgeBaseRow.knowledge_base_reference == reference,
                )
            )
            await session.commit()
            return bool(cast(CursorResult[Any], result).rowcount)
