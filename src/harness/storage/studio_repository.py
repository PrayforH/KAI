"""PostgreSQL adapter for tenant-and-owner-scoped Agent Studio drafts."""

from typing import Any, cast

from sqlalchemy import CursorResult, delete, select, update
from sqlalchemy.exc import IntegrityError

from harness.core.errors import ConflictError, NotFoundError
from harness.storage.database import SessionFactory
from harness.storage.models import AgentDraftRow
from harness.studio.models import AgentDraft, AgentDraftSummary

AGENT_DRAFT_SCHEMA_VERSION = 1


def _draft_payload(draft: AgentDraft) -> dict[str, Any]:
    return draft.model_dump(mode="json", by_alias=True)


def _load_draft(row: AgentDraftRow) -> AgentDraft:
    if row.schema_version != AGENT_DRAFT_SCHEMA_VERSION:
        raise ValueError(
            "Unsupported Agent Draft schema version: "
            f"{row.schema_version}; expected={AGENT_DRAFT_SCHEMA_VERSION}"
        )
    draft = AgentDraft.model_validate(row.payload)
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

    def __init__(self, sessions: SessionFactory) -> None:
        self._sessions = sessions

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
                    payload=_draft_payload(draft),
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
            return _load_draft(row)

    async def add_child(
        self, expected_revision: int, parent: AgentDraft, child: AgentDraft
    ) -> None:
        if parent.revision != expected_revision + 1:
            raise ConflictError("Agent draft replacement must increment revision once")
        async with self._sessions() as session:
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
                    payload=_draft_payload(parent),
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
                    payload=_draft_payload(child),
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
            return [_load_draft(row) for row in rows]

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
            return [_load_draft(row) for row in rows]

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
                payload=_draft_payload(draft),
            )
        )
        async with self._sessions() as session:
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
                await session.delete(row)
            await session.commit()
            return len(rows)
