"""PostgreSQL workspace Agent, Release and ACL persistence."""

from typing import Any, cast

from sqlalchemy import CursorResult, delete, select
from sqlalchemy.exc import IntegrityError

from harness.core.errors import ConflictError, NotFoundError
from harness.sharing.models import (
    AgentAcl,
    AgentPermission,
    AgentRelease,
    AgentScope,
    GranteeType,
    GroupMember,
    UserGroup,
    WorkspaceAgent,
)
from harness.sharing.workspace_repositories import WorkspaceAgentRepository
from harness.storage.database import SessionFactory
from harness.storage.models import (
    AgentAclRow,
    AgentReleaseRow,
    GroupMemberRow,
    UserGroupRow,
    WorkspaceAgentRow,
)


async def _commit_add(session: Any, *, message: str) -> None:
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise ConflictError(message) from error


class PostgresWorkspaceAgentRepository(WorkspaceAgentRepository):
    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

    async def add_agent(self, agent: WorkspaceAgent) -> None:
        async with self._sessions() as session:
            session.add(
                WorkspaceAgentRow(
                    tenant_id=agent.tenant_id,
                    agent_id=agent.agent_id,
                    scope=agent.scope.value,
                    owner_user_id=agent.owner_user_id,
                    space_id=agent.space_id,
                    name=agent.name,
                    current_version=agent.current_version,
                    status=agent.status.value,
                    created_at=agent.created_at,
                    updated_at=agent.updated_at,
                    payload=agent.model_dump(mode="json", by_alias=True),
                )
            )
            await _commit_add(
                session,
                message=f"workspace agent already exists: {agent.agent_id}",
            )

    async def get_agent(self, tenant_id: str, agent_id: str) -> WorkspaceAgent:
        async with self._sessions() as session:
            row = await session.get(WorkspaceAgentRow, (tenant_id, agent_id))
            if row is None:
                raise NotFoundError(f"workspace agent not found: {agent_id}")
            return WorkspaceAgent.model_validate(row.payload)

    async def _find_agent(
        self,
        *,
        tenant_id: str,
        scope: AgentScope,
        owner_user_id: str | None = None,
        space_id: str | None = None,
        name: str,
    ) -> WorkspaceAgent | None:
        statement = select(WorkspaceAgentRow.payload).where(
            WorkspaceAgentRow.tenant_id == tenant_id,
            WorkspaceAgentRow.scope == scope.value,
            WorkspaceAgentRow.name == name,
        )
        if owner_user_id is not None:
            statement = statement.where(WorkspaceAgentRow.owner_user_id == owner_user_id)
        if space_id is not None:
            statement = statement.where(WorkspaceAgentRow.space_id == space_id)
        async with self._sessions() as session:
            payload = (await session.scalars(statement)).first()
            return None if payload is None else WorkspaceAgent.model_validate(payload)

    async def get_personal_agent(
        self, tenant_id: str, owner_user_id: str, name: str
    ) -> WorkspaceAgent | None:
        return await self._find_agent(
            tenant_id=tenant_id,
            scope=AgentScope.PERSONAL,
            owner_user_id=owner_user_id,
            name=name,
        )

    async def get_workspace_agent(
        self, tenant_id: str, space_id: str, name: str
    ) -> WorkspaceAgent | None:
        return await self._find_agent(
            tenant_id=tenant_id,
            scope=AgentScope.WORKSPACE,
            space_id=space_id,
            name=name,
        )

    async def list_agents_for_space(
        self, tenant_id: str, space_id: str
    ) -> list[WorkspaceAgent]:
        statement = (
            select(WorkspaceAgentRow.payload)
            .where(
                WorkspaceAgentRow.tenant_id == tenant_id,
                WorkspaceAgentRow.scope == AgentScope.WORKSPACE.value,
                WorkspaceAgentRow.space_id == space_id,
            )
            .order_by(WorkspaceAgentRow.name, WorkspaceAgentRow.agent_id)
        )
        async with self._sessions() as session:
            return [
                WorkspaceAgent.model_validate(payload)
                for payload in (await session.scalars(statement)).all()
            ]

    async def list_personal_agents(
        self, tenant_id: str, owner_user_id: str
    ) -> list[WorkspaceAgent]:
        statement = (
            select(WorkspaceAgentRow.payload)
            .where(
                WorkspaceAgentRow.tenant_id == tenant_id,
                WorkspaceAgentRow.scope == AgentScope.PERSONAL.value,
                WorkspaceAgentRow.owner_user_id == owner_user_id,
            )
            .order_by(WorkspaceAgentRow.name, WorkspaceAgentRow.agent_id)
        )
        async with self._sessions() as session:
            return [
                WorkspaceAgent.model_validate(payload)
                for payload in (await session.scalars(statement)).all()
            ]

    async def update_agent(self, agent: WorkspaceAgent) -> None:
        async with self._sessions() as session:
            row = await session.get(
                WorkspaceAgentRow,
                (agent.tenant_id, agent.agent_id),
                with_for_update=True,
            )
            if row is None:
                raise NotFoundError(f"workspace agent not found: {agent.agent_id}")
            row.current_version = agent.current_version
            row.status = agent.status.value
            row.updated_at = agent.updated_at
            row.payload = agent.model_dump(mode="json", by_alias=True)
            await session.commit()

    async def add_release(self, release: AgentRelease) -> None:
        async with self._sessions() as session:
            session.add(
                AgentReleaseRow(
                    tenant_id=release.tenant_id,
                    space_id=release.space_id,
                    agent_id=release.agent_id,
                    version=release.version,
                    source_owner_user_id=release.source_owner_user_id,
                    source_name=release.source_name,
                    promoted_by=release.promoted_by,
                    created_at=release.created_at,
                    payload=release.model_dump(mode="json", by_alias=True),
                )
            )
            await _commit_add(
                session,
                message=(
                    f"agent release already exists: "
                    f"{release.agent_id}@{release.version}"
                ),
            )

    async def get_release(
        self, tenant_id: str, space_id: str, agent_id: str, version: str
    ) -> AgentRelease:
        async with self._sessions() as session:
            row = await session.get(
                AgentReleaseRow, (tenant_id, space_id, agent_id, version)
            )
            if row is None:
                raise NotFoundError(f"agent release not found: {agent_id}@{version}")
            return AgentRelease.model_validate(row.payload)

    async def get_release_by_source(
        self,
        tenant_id: str,
        space_id: str,
        source_owner_user_id: str,
        source_name: str,
        version: str,
    ) -> AgentRelease | None:
        statement = select(AgentReleaseRow.payload).where(
            AgentReleaseRow.tenant_id == tenant_id,
            AgentReleaseRow.space_id == space_id,
            AgentReleaseRow.source_owner_user_id == source_owner_user_id,
            AgentReleaseRow.source_name == source_name,
            AgentReleaseRow.version == version,
        )
        async with self._sessions() as session:
            payload = (await session.scalars(statement)).first()
            return None if payload is None else AgentRelease.model_validate(payload)

    async def list_releases(
        self, tenant_id: str, space_id: str, agent_id: str
    ) -> list[AgentRelease]:
        statement = (
            select(AgentReleaseRow.payload)
            .where(
                AgentReleaseRow.tenant_id == tenant_id,
                AgentReleaseRow.space_id == space_id,
                AgentReleaseRow.agent_id == agent_id,
            )
            .order_by(AgentReleaseRow.version)
        )
        async with self._sessions() as session:
            return [
                AgentRelease.model_validate(payload)
                for payload in (await session.scalars(statement)).all()
            ]

    async def delete_release(
        self, tenant_id: str, space_id: str, agent_id: str, version: str
    ) -> bool:
        async with self._sessions() as session:
            result = await session.execute(
                delete(AgentReleaseRow).where(
                    AgentReleaseRow.tenant_id == tenant_id,
                    AgentReleaseRow.space_id == space_id,
                    AgentReleaseRow.agent_id == agent_id,
                    AgentReleaseRow.version == version,
                )
            )
            await session.commit()
            return bool(cast(CursorResult[Any], result).rowcount)

    async def add_acl(self, acl: AgentAcl) -> None:
        async with self._sessions() as session:
            session.add(
                AgentAclRow(
                    tenant_id=acl.tenant_id,
                    agent_id=acl.agent_id,
                    grantee_type=acl.grantee_type.value,
                    grantee_id=acl.grantee_id,
                    permission=acl.permission.value,
                    granted_by=acl.granted_by,
                    created_at=acl.created_at,
                    payload=acl.model_dump(mode="json", by_alias=True),
                )
            )
            await _commit_add(
                session,
                message=(
                    f"agent ACL already exists: {acl.agent_id} "
                    f"{acl.grantee_type.value}:{acl.grantee_id} {acl.permission.value}"
                ),
            )

    async def list_acls(self, tenant_id: str, agent_id: str) -> list[AgentAcl]:
        statement = (
            select(AgentAclRow.payload)
            .where(
                AgentAclRow.tenant_id == tenant_id,
                AgentAclRow.agent_id == agent_id,
            )
            .order_by(AgentAclRow.grantee_type, AgentAclRow.grantee_id, AgentAclRow.permission)
        )
        async with self._sessions() as session:
            return [
                AgentAcl.model_validate(payload)
                for payload in (await session.scalars(statement)).all()
            ]

    async def delete_acl(
        self,
        tenant_id: str,
        agent_id: str,
        grantee_type: GranteeType,
        grantee_id: str,
        permission: AgentPermission,
    ) -> bool:
        async with self._sessions() as session:
            result = await session.execute(
                delete(AgentAclRow).where(
                    AgentAclRow.tenant_id == tenant_id,
                    AgentAclRow.agent_id == agent_id,
                    AgentAclRow.grantee_type == grantee_type.value,
                    AgentAclRow.grantee_id == grantee_id,
                    AgentAclRow.permission == permission.value,
                )
            )
            await session.commit()
            return bool(cast(CursorResult[Any], result).rowcount)

    async def add_group(self, group: UserGroup) -> None:
        async with self._sessions() as session:
            session.add(
                UserGroupRow(
                    tenant_id=group.tenant_id,
                    group_id=group.group_id,
                    name=group.name,
                    created_at=group.created_at,
                    payload=group.model_dump(mode="json", by_alias=True),
                )
            )
            await _commit_add(session, message=f"user group already exists: {group.group_id}")

    async def get_group(self, tenant_id: str, group_id: str) -> UserGroup:
        async with self._sessions() as session:
            row = await session.get(UserGroupRow, (tenant_id, group_id))
            if row is None:
                raise NotFoundError(f"user group not found: {group_id}")
            return UserGroup.model_validate(row.payload)

    async def list_groups(self, tenant_id: str) -> list[UserGroup]:
        statement = (
            select(UserGroupRow.payload)
            .where(UserGroupRow.tenant_id == tenant_id)
            .order_by(UserGroupRow.name, UserGroupRow.group_id)
        )
        async with self._sessions() as session:
            return [
                UserGroup.model_validate(payload)
                for payload in (await session.scalars(statement)).all()
            ]

    async def delete_group(self, tenant_id: str, group_id: str) -> bool:
        async with self._sessions() as session:
            result = await session.execute(
                delete(UserGroupRow).where(
                    UserGroupRow.tenant_id == tenant_id,
                    UserGroupRow.group_id == group_id,
                )
            )
            await session.commit()
            return bool(cast(CursorResult[Any], result).rowcount)

    async def add_group_member(self, member: GroupMember) -> None:
        async with self._sessions() as session:
            session.add(
                GroupMemberRow(
                    tenant_id=member.tenant_id,
                    group_id=member.group_id,
                    user_id=member.user_id,
                    created_at=member.created_at,
                    payload=member.model_dump(mode="json", by_alias=True),
                )
            )
            await _commit_add(
                session,
                message=f"group member already exists: {member.group_id}:{member.user_id}",
            )

    async def list_group_members(
        self, tenant_id: str, group_id: str
    ) -> list[GroupMember]:
        statement = (
            select(GroupMemberRow.payload)
            .where(
                GroupMemberRow.tenant_id == tenant_id,
                GroupMemberRow.group_id == group_id,
            )
            .order_by(GroupMemberRow.user_id)
        )
        async with self._sessions() as session:
            return [
                GroupMember.model_validate(payload)
                for payload in (await session.scalars(statement)).all()
            ]

    async def list_groups_for_user(self, tenant_id: str, user_id: str) -> list[str]:
        statement = (
            select(GroupMemberRow.group_id)
            .where(
                GroupMemberRow.tenant_id == tenant_id,
                GroupMemberRow.user_id == user_id,
            )
            .order_by(GroupMemberRow.group_id)
        )
        async with self._sessions() as session:
            return list((await session.scalars(statement)).all())

    async def delete_group_member(
        self, tenant_id: str, group_id: str, user_id: str
    ) -> bool:
        async with self._sessions() as session:
            result = await session.execute(
                delete(GroupMemberRow).where(
                    GroupMemberRow.tenant_id == tenant_id,
                    GroupMemberRow.group_id == group_id,
                    GroupMemberRow.user_id == user_id,
                )
            )
            await session.commit()
            return bool(cast(CursorResult[Any], result).rowcount)
