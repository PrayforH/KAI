"""PostgreSQL adapter for tenant-and-owner-scoped Agent Studio drafts."""

from typing import Any, cast

from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from harness.core.errors import ConflictError, NotFoundError
from harness.storage.database import SessionFactory
from harness.storage.models import AgentDraftRevisionRow, AgentDraftRow
from harness.storage.skill_blobs import SkillBlobStore
from harness.studio.catalog import RETIRED_PLATFORM_MCP_REFERENCES
from harness.studio.models import AgentDraft, AgentDraftSummary, DraftRevisionSummary

AGENT_DRAFT_SCHEMA_VERSION = 1


def _draft_payload(draft: AgentDraft) -> dict[str, Any]:
    return draft.model_dump(mode="json", by_alias=True)


def _strip_retired_mcp_references(draft: AgentDraft) -> AgentDraft:
    """Drop retired platform MCP references so stored drafts stay compilable."""

    if not any(item in RETIRED_PLATFORM_MCP_REFERENCES for item in draft.spec.mcp_servers):
        return draft
    return draft.model_copy(
        update={
            "spec": draft.spec.model_copy(
                update={
                    "mcp_servers": tuple(
                        item
                        for item in draft.spec.mcp_servers
                        if item not in RETIRED_PLATFORM_MCP_REFERENCES
                    )
                }
            )
        }
    )


def _load_draft(row: AgentDraftRow, payload: dict[str, Any] | None = None) -> AgentDraft:
    if row.schema_version != AGENT_DRAFT_SCHEMA_VERSION:
        raise ValueError(
            "Unsupported Agent Draft schema version: "
            f"{row.schema_version}; expected={AGENT_DRAFT_SCHEMA_VERSION}"
        )
    draft = _strip_retired_mcp_references(
        AgentDraft.model_validate(payload if payload is not None else row.payload)
    )
    if draft.agent_id is None and row.agent_id is not None:
        draft = draft.model_copy(update={"agent_id": row.agent_id})
    if draft.space_id is None and row.space_id is not None:
        draft = draft.model_copy(update={"space_id": row.space_id})
    if (
        draft.tenant_id != row.tenant_id
        or draft.created_by != row.owner_user_id
        or draft.draft_id != row.draft_id
        or draft.revision != row.revision
        or draft.spec.name != row.name
        or draft.updated_at != row.updated_at
    ):
        raise ValueError(f"Corrupt Agent Draft persistence envelope: {row.draft_id}")
    return draft


def _summary_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Extract card-sized facts from both current and historical draft payloads."""

    spec = payload.get("spec")
    spec = cast(dict[str, Any], spec) if isinstance(spec, dict) else {}
    task_contract = spec.get("taskContract")
    task_contract = cast(dict[str, Any], task_contract) if isinstance(task_contract, dict) else {}

    outputs = task_contract.get("outputs")
    outputs = cast(list[Any], outputs) if isinstance(outputs, list) else []
    constraints = task_contract.get("constraints")
    constraints = cast(list[Any], constraints) if isinstance(constraints, list) else []
    skills = spec.get("skills")
    skills = cast(list[Any], skills) if isinstance(skills, list) else []
    builtin_tools = spec.get("builtinTools")
    builtin_tools = cast(list[Any], builtin_tools) if isinstance(builtin_tools, list) else []
    python_tools = spec.get("pythonTools")
    python_tools = cast(list[Any], python_tools) if isinstance(python_tools, list) else []
    mcp_servers = spec.get("mcpServers")
    mcp_servers = cast(list[Any], mcp_servers) if isinstance(mcp_servers, list) else []
    mcp_servers = [item for item in mcp_servers if item not in RETIRED_PLATFORM_MCP_REFERENCES]

    return {
        "parentDraftId": payload.get("parentDraftId"),
        "goal": task_contract.get("goal") or spec.get("description") or "完成已配置任务",
        "primaryOutput": outputs[0] if outputs else "按 System Prompt 生成可核验结果",
        "primaryConstraint": constraints[0] if constraints else None,
        "skillCount": len(skills),
        "toolCount": len(builtin_tools) + len(python_tools) + len(mcp_servers),
        "networkToolsEnabled": bool({"WebSearch", "WebFetch"}.intersection(builtin_tools)),
    }


class PostgresAgentDraftRepository:
    """Durable Draft storage with owner isolation and atomic revision CAS."""

    def __init__(self, sessions: SessionFactory, skill_blobs: SkillBlobStore | None = None) -> None:
        self._sessions = sessions
        self._skill_blobs = skill_blobs

    async def _pack(self, draft: AgentDraft) -> dict[str, Any]:
        payload = _draft_payload(draft)
        return (await self._skill_blobs.transform(draft.tenant_id, payload)
                if self._skill_blobs else payload)

    async def _unpack(self, tenant_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return (await self._skill_blobs.transform(tenant_id, payload, inline=True)
                if self._skill_blobs else payload)

    async def _load(self, row: AgentDraftRow) -> AgentDraft:
        return _load_draft(row, await self._unpack(row.tenant_id, row.payload))

    async def _archive_current(
        self, session: AsyncSession, draft: AgentDraft, expected_revision: int,
    ) -> None:
        row = await session.scalar(select(AgentDraftRow).where(
            AgentDraftRow.tenant_id == draft.tenant_id,
            AgentDraftRow.owner_user_id == draft.created_by,
            AgentDraftRow.draft_id == draft.draft_id,
        ).with_for_update())
        if row is None:
            raise NotFoundError(f"Agent draft not found: {draft.draft_id}")
        if row.revision != expected_revision:
            raise ConflictError("Agent draft revision changed")
        key = (row.tenant_id, row.owner_user_id, row.draft_id, row.revision)
        if await session.get(AgentDraftRevisionRow, key) is None:
            session.add(AgentDraftRevisionRow(
                tenant_id=row.tenant_id, owner_user_id=row.owner_user_id,
                draft_id=row.draft_id, revision=row.revision,
                updated_at=row.updated_at, payload=(await self._skill_blobs.transform(
                    row.tenant_id, row.payload) if self._skill_blobs else dict(row.payload)),
            ))

    async def list_revisions(
        self, tenant_id: str, owner_user_id: str, draft_id: str,
        *, before_revision: int | None = None, limit: int = 50,
    ) -> list[DraftRevisionSummary]:
        current = await self.get(tenant_id, owner_user_id, draft_id)
        statement = select(AgentDraftRevisionRow.revision, AgentDraftRevisionRow.updated_at).where(
            AgentDraftRevisionRow.tenant_id == tenant_id,
            AgentDraftRevisionRow.owner_user_id == owner_user_id,
            AgentDraftRevisionRow.draft_id == draft_id,
            AgentDraftRevisionRow.revision < current.revision,
        )
        if before_revision is not None:
            statement = statement.where(AgentDraftRevisionRow.revision < before_revision)
        async with self._sessions() as session:
            rows = (await session.execute(statement.order_by(
                AgentDraftRevisionRow.revision.desc()).limit(limit))).all()
        values = [DraftRevisionSummary(revision=r[0], updatedAt=r[1]) for r in rows]
        if before_revision is None or current.revision < before_revision:
            values.insert(0, DraftRevisionSummary(
                revision=current.revision, updatedAt=current.updated_at))
        return values[:limit]

    async def get_revision(
        self, tenant_id: str, owner_user_id: str, draft_id: str, revision: int,
    ) -> AgentDraft:
        current = await self.get(tenant_id, owner_user_id, draft_id)
        if current.revision == revision:
            return current
        async with self._sessions() as session:
            row = await session.get(AgentDraftRevisionRow,
                                    (tenant_id, owner_user_id, draft_id, revision))
            if row is None:
                raise NotFoundError("草稿修订未留存")
            return AgentDraft.model_validate(await self._unpack(tenant_id, row.payload))

    async def add(self, draft: AgentDraft) -> None:
        async with self._sessions() as session:
            session.add(
                AgentDraftRow(
                    tenant_id=draft.tenant_id,
                    owner_user_id=draft.created_by,
                    draft_id=draft.draft_id,
                    agent_id=draft.agent_id,
                    space_id=draft.space_id,
                    name=draft.spec.name,
                    revision=draft.revision,
                    schema_version=AGENT_DRAFT_SCHEMA_VERSION,
                    updated_at=draft.updated_at,
                    payload=await self._pack(draft),
                )
            )
            try:
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                raise ConflictError(f"Agent draft already exists: {draft.draft_id}") from error

    async def get(self, tenant_id: str, owner_user_id: str, draft_id: str) -> AgentDraft:
        async with self._sessions() as session:
            row = await session.get(AgentDraftRow, (tenant_id, owner_user_id, draft_id))
            if row is None:
                raise NotFoundError(f"Agent draft not found: {draft_id}")
            return await self._load(row)

    async def add_child(
        self, expected_revision: int, parent: AgentDraft, child: AgentDraft
    ) -> None:
        if parent.revision != expected_revision + 1:
            raise ConflictError("Agent draft replacement must increment revision once")
        async with self._sessions() as session:
            await self._archive_current(session, parent, expected_revision)
            result = await session.execute(
                update(AgentDraftRow)
                .where(
                    AgentDraftRow.tenant_id == parent.tenant_id,
                    AgentDraftRow.owner_user_id == parent.created_by,
                    AgentDraftRow.draft_id == parent.draft_id,
                    AgentDraftRow.revision == expected_revision,
                )
                .values(
                    revision=parent.revision,
                    updated_at=parent.updated_at,
                    payload=await self._pack(parent),
                )
            )
            if not cast(CursorResult[Any], result).rowcount:
                raise ConflictError("父智能体已更新，请刷新后重试")
            session.add(
                AgentDraftRow(
                    tenant_id=child.tenant_id,
                    owner_user_id=child.created_by,
                    draft_id=child.draft_id,
                    agent_id=child.agent_id,
                    space_id=child.space_id,
                    name=child.spec.name,
                    revision=child.revision,
                    schema_version=AGENT_DRAFT_SCHEMA_VERSION,
                    updated_at=child.updated_at,
                    payload=await self._pack(child),
                )
            )
            try:
                await session.commit()
            except IntegrityError as error:
                await session.rollback()
                raise ConflictError("子智能体已存在，请刷新后重试") from error

    async def list_for_user(self, tenant_id: str, owner_user_id: str) -> list[AgentDraft]:
        statement = (
            select(AgentDraftRow)
            .where(
                AgentDraftRow.tenant_id == tenant_id,
                AgentDraftRow.owner_user_id == owner_user_id,
            )
            .order_by(AgentDraftRow.updated_at.desc(), AgentDraftRow.draft_id.desc())
        )
        async with self._sessions() as session:
            rows = (await session.scalars(statement)).all()
            return [await self._load(row) for row in rows]

    async def list_summaries(self, tenant_id: str, owner_user_id: str) -> list[AgentDraftSummary]:
        statement = (
            select(
                AgentDraftRow.draft_id,
                AgentDraftRow.agent_id,
                AgentDraftRow.space_id,
                AgentDraftRow.name,
                AgentDraftRow.payload["spec"]["displayName"].as_string(),
                AgentDraftRow.payload["spec"]["domain"].as_string(),
                AgentDraftRow.payload["spec"]["version"].as_string(),
                AgentDraftRow.payload["spec"]["template"].as_string(),
                AgentDraftRow.revision,
                AgentDraftRow.updated_at,
                AgentDraftRow.payload["publishedVersion"].as_string(),
                AgentDraftRow.payload,
            )
            .where(
                AgentDraftRow.tenant_id == tenant_id,
                AgentDraftRow.owner_user_id == owner_user_id,
            )
            .order_by(AgentDraftRow.updated_at.desc(), AgentDraftRow.draft_id.desc())
        )
        async with self._sessions() as session:
            rows = (await session.execute(statement)).all()
        return [
            AgentDraftSummary(
                draftId=row[0],
                agentId=row[1],
                spaceId=row[2],
                name=row[3],
                displayName=row[4],
                domain=row[5],
                version=row[6],
                template=row[7],
                revision=row[8],
                updatedAt=row[9],
                publishedVersion=row[10],
                **_summary_fields(cast(dict[str, Any], row[11])),
            )
            for row in rows
        ]

    async def list_all_for_tenant(self, tenant_id: str) -> list[AgentDraft]:
        statement = (
            select(AgentDraftRow)
            .where(AgentDraftRow.tenant_id == tenant_id)
            .order_by(AgentDraftRow.updated_at.desc(), AgentDraftRow.draft_id.desc())
        )
        async with self._sessions() as session:
            rows = (await session.scalars(statement)).all()
            return [await self._load(row) for row in rows]

    async def replace(self, expected_revision: int, draft: AgentDraft) -> None:
        if draft.revision != expected_revision + 1:
            raise ConflictError("Agent draft replacement must increment revision once")
        statement = (
            update(AgentDraftRow)
            .where(
                AgentDraftRow.tenant_id == draft.tenant_id,
                AgentDraftRow.owner_user_id == draft.created_by,
                AgentDraftRow.draft_id == draft.draft_id,
                AgentDraftRow.revision == expected_revision,
            )
            .values(
                name=draft.spec.name,
                agent_id=draft.agent_id,
                space_id=draft.space_id,
                revision=draft.revision,
                schema_version=AGENT_DRAFT_SCHEMA_VERSION,
                updated_at=draft.updated_at,
                payload=await self._pack(draft),
            )
        )
        async with self._sessions() as session:
            await self._archive_current(session, draft, expected_revision)
            result = await session.execute(statement)
            if cast(CursorResult[Any], result).rowcount:
                await session.commit()
                return
            actual_revision = await session.scalar(
                select(AgentDraftRow.revision).where(
                    AgentDraftRow.tenant_id == draft.tenant_id,
                    AgentDraftRow.owner_user_id == draft.created_by,
                    AgentDraftRow.draft_id == draft.draft_id,
                )
            )
            await session.rollback()
            if actual_revision is None:
                raise NotFoundError(f"Agent draft not found: {draft.draft_id}")
            raise ConflictError(
                "Agent draft revision changed: "
                f"expected={expected_revision} actual={actual_revision}"
            )

    async def delete(
        self,
        tenant_id: str,
        owner_user_id: str,
        draft_id: str,
        expected_revision: int,
    ) -> None:
        statement = delete(AgentDraftRow).where(
            AgentDraftRow.tenant_id == tenant_id,
            AgentDraftRow.owner_user_id == owner_user_id,
            AgentDraftRow.draft_id == draft_id,
            AgentDraftRow.revision == expected_revision,
        )
        async with self._sessions() as session:
            result = await session.execute(statement)
            if cast(CursorResult[Any], result).rowcount:
                await session.execute(delete(AgentDraftRevisionRow).where(
                    AgentDraftRevisionRow.tenant_id == tenant_id,
                    AgentDraftRevisionRow.owner_user_id == owner_user_id,
                    AgentDraftRevisionRow.draft_id == draft_id,
                ))
                await session.commit()
                return
            actual_revision = await session.scalar(
                select(AgentDraftRow.revision).where(
                    AgentDraftRow.tenant_id == tenant_id,
                    AgentDraftRow.owner_user_id == owner_user_id,
                    AgentDraftRow.draft_id == draft_id,
                )
            )
            await session.rollback()
            if actual_revision is None:
                raise NotFoundError(f"Agent draft not found: {draft_id}")
            raise ConflictError(
                "Agent draft revision changed: "
                f"expected={expected_revision} actual={actual_revision}"
            )

    async def get_by_agent(self, tenant_id: str, agent_id: str) -> AgentDraft | None:
        statement = select(AgentDraftRow).where(
            AgentDraftRow.tenant_id == tenant_id,
            AgentDraftRow.agent_id == agent_id,
        )
        async with self._sessions() as session:
            row = (await session.scalars(statement)).first()
            return None if row is None else _load_draft(row)

    async def get_shared(self, tenant_id: str, draft_id: str) -> AgentDraft | None:
        statement = select(AgentDraftRow).where(
            AgentDraftRow.tenant_id == tenant_id,
            AgentDraftRow.draft_id == draft_id,
            AgentDraftRow.space_id.is_not(None),
        )
        async with self._sessions() as session:
            row = (await session.scalars(statement)).first()
            return None if row is None else _load_draft(row)

    async def move_owner(
        self, tenant_id: str, from_user_id: str, to_user_id: str, name: str
    ) -> int:
        if from_user_id == to_user_id:
            return 0
        async with self._sessions() as session:
            rows = (
                await session.scalars(
                    select(AgentDraftRow)
                    .where(
                        AgentDraftRow.tenant_id == tenant_id,
                        AgentDraftRow.owner_user_id == from_user_id,
                        AgentDraftRow.name == name,
                    )
                    .with_for_update()
                )
            ).all()
            for row in rows:
                payload = dict(row.payload)
                payload["createdBy"] = to_user_id
                payload["updatedBy"] = to_user_id
                session.add(
                    AgentDraftRow(
                        tenant_id=tenant_id,
                        owner_user_id=to_user_id,
                        draft_id=row.draft_id,
                        agent_id=row.agent_id,
                        space_id=row.space_id,
                        name=row.name,
                        revision=row.revision,
                        schema_version=row.schema_version,
                        updated_at=row.updated_at,
                        payload=payload,
                    )
                )
                await session.execute(update(AgentDraftRevisionRow).where(
                    AgentDraftRevisionRow.tenant_id == tenant_id,
                    AgentDraftRevisionRow.owner_user_id == from_user_id,
                    AgentDraftRevisionRow.draft_id == row.draft_id,
                ).values(owner_user_id=to_user_id))
                await session.delete(row)
            await session.commit()
            return len(rows)
