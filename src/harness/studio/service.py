"""Agent Studio draft lifecycle and immutable publication orchestration."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from harness.auth.audit import AuditService
from harness.core.errors import ConflictError, NotFoundError
from harness.core.models import AgentVersion, AgentVersionStatus
from harness.core.ports import AgentIdentityProvider, AgentRegistry
from harness.evals.suite import EvalCase
from harness.knowledge.service import KnowledgeService
from harness.sharing.models import (
    AgentPermission,
    WorkspaceAgent,
)
from harness.studio.agent_builder import (
    AgentBuilderPatch,
    AgentBuilderPatchRequest,
    CreateTaskDrivenDraftRequest,
    TaskDrivenDraftResult,
    build_agent_patch,
    configure_task_driven_draft,
    summarize_agent_display_name,
    summarize_agent_name,
)
from harness.studio.builder_conversation import (
    BUILDER_AUTO_SYSTEM_PROMPT,
    BUILDER_SYSTEM_PROMPT,
    BuilderApplyRequest,
    BuilderConversationReply,
    BuilderConversationRequest,
    apply_builder_changes,
    parse_builder_reply,
)
from harness.studio.bundle_import import AgentBundleImportError, parse_agent_bundle
from harness.studio.catalog_service import CapabilityCatalogService
from harness.studio.compiler import (
    AgentDraftCompiler,
    CompiledAgentDraft,
    DraftCompilationError,
)
from harness.studio.factory import create_draft_spec
from harness.studio.model_configuration import ModelConfigurationService
from harness.studio.models import (
    AgentDraft,
    AgentDraftPlacementRequest,
    AgentDraftSpec,
    AgentDraftSummary,
    AgentTemplate,
    CapabilityCatalog,
    CreateAgentDraftRequest,
    CreatedInternalSubagent,
    CreateInternalSubagentRequest,
    DraftSkill,
    DraftSkillFile,
    DraftSubagent,
    DraftValidationResult,
    ImportedAgentBundle,
    ImportedSkill,
    ReplaceAgentDraftRequest,
    ValidationIssue,
    ValidationSeverity,
)
from harness.studio.nexau_export import NexauAgentArchive, export_nexau_agent
from harness.studio.platform_skills import draft_skill_content_hash
from harness.studio.repositories import AgentDraftRepository

_EDITOR_SKILL_FILE_LIMIT = 200
_EDITOR_INLINE_TEXT_BYTES = 64 * 1024
_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


@dataclass(frozen=True, slots=True)
class PreviewGraphNode:
    draft: AgentDraft
    compiled: CompiledAgentDraft
    preview_version: str


@dataclass(frozen=True, slots=True)
class CompiledPreviewGraph:
    root_draft: AgentDraft
    root: CompiledAgentDraft
    dependencies: tuple[PreviewGraphNode, ...]


def _next_patch_version(version: str) -> str:
    match = _SEMVER.fullmatch(version)
    if match is None:
        return version
    return f"{match[1]}.{match[2]}.{int(match[3]) + 1}"


def _auto_version_modified_release(
    current: AgentDraft,
    candidate: AgentDraftSpec,
) -> AgentDraftSpec:
    if (
        current.published_version is not None
        and candidate.version == current.published_version
        and candidate != current.spec
    ):
        return candidate.model_copy(update={"version": _next_patch_version(candidate.version)})
    return candidate


def _skill_file_bytes(file: DraftSkillFile) -> bytes:
    if file.content is not None:
        return file.content.encode("utf-8")
    if file.content_base64 is not None:
        return base64.b64decode(file.content_base64, validate=True)
    raise ValueError(f"Retained Skill file has no inline content: {file.path}")


def _retained_skill_file(file: DraftSkillFile) -> DraftSkillFile:
    payload = _skill_file_bytes(file)
    return DraftSkillFile(
        path=file.path,
        retained=True,
        sizeBytes=len(payload),
        contentSha256=hashlib.sha256(payload).hexdigest(),
        binary=file.content_base64 is not None,
    )


def compact_draft_for_editor(draft: AgentDraft) -> AgentDraft:
    """Bound editor payload size while retaining every server-side Skill file."""

    skills: list[DraftSkill] = []
    for skill in draft.spec.skills:
        visible: list[DraftSkillFile] = []
        for file in skill.files[:_EDITOR_SKILL_FILE_LIMIT]:
            if file.retained:
                visible.append(file)
                continue
            if (
                file.content is not None
                and len(file.content.encode("utf-8")) <= _EDITOR_INLINE_TEXT_BYTES
            ):
                visible.append(file)
            else:
                visible.append(_retained_skill_file(file))
        skills.append(
            skill.model_copy(
                update={
                    "files": tuple(visible),
                    "file_count": len(skill.files),
                    "files_truncated": len(skill.files) > len(visible),
                }
            )
        )
    return draft.model_copy(
        update={"spec": draft.spec.model_copy(update={"skills": tuple(skills)})}
    )


def _merge_editor_skill(current: DraftSkill | None, incoming: DraftSkill) -> DraftSkill:
    current_files = {file.path: file for file in current.files} if current else {}
    incoming_files: dict[str, DraftSkillFile] = {}
    for file in incoming.files:
        if not file.retained:
            incoming_files[file.path] = file
            continue
        retained = current_files.get(file.path)
        if retained is None:
            raise ConflictError(
                f"Retained Skill file no longer exists: {incoming.name}/{file.path}"
            )
        payload = _skill_file_bytes(retained)
        if (
            len(payload) != file.size_bytes
            or hashlib.sha256(payload).hexdigest() != file.content_sha256
        ):
            raise ConflictError(f"Retained Skill file changed: {incoming.name}/{file.path}")
        incoming_files[file.path] = retained

    if incoming.files_truncated:
        if current is None:
            raise ConflictError(f"Truncated Skill has no stored source: {incoming.name}")
        merged = [incoming_files.get(file.path, file) for file in current.files]
    else:
        merged = [incoming_files[file.path] for file in incoming.files]
    merged_skill = incoming.model_copy(
        update={
            "files": tuple(merged),
            "file_count": None,
            "files_truncated": False,
        }
    )
    if (
        merged_skill.source is not None
        and draft_skill_content_hash(merged_skill) != merged_skill.source.content_hash
    ):
        # Preserve where the Skill came from, but stop presenting tenant edits
        # as the exact reviewed platform snapshot.
        return merged_skill.model_copy(
            update={"source": merged_skill.source.model_copy(update={"modified": True})}
        )
    return merged_skill


class SharedDraftPermissionChecker(Protocol):
    """Space-side checks for drafts bound to a workspace Agent."""

    async def require_draft_permission(
        self,
        tenant_id: str,
        user_id: str,
        space_id: str,
        agent_id: str,
        permission: AgentPermission,
    ) -> None: ...

    async def list_agents(
        self, tenant_id: str, user_id: str, space_id: str
    ) -> list[WorkspaceAgent]: ...

    async def get_space_agent(
        self, tenant_id: str, user_id: str, space_id: str, agent_id: str
    ) -> WorkspaceAgent: ...

    async def share_release(
        self,
        tenant_id: str,
        user_id: str,
        space_id: str,
        name: str,
        version: str,
    ) -> None: ...


class AgentBundlePublisher(Protocol):
    async def publish_bundle(
        self,
        tenant_id: str,
        owner_user_id: str,
        content: bytes,
        *,
        agent_id: str | None = None,
    ) -> AgentVersion: ...


class StudioPublisherNotConfiguredError(RuntimeError):
    pass


class StudioPublicationConflictError(ConflictError):
    pass


class AgentStudioService:
    def __init__(
        self,
        repository: AgentDraftRepository,
        compiler: AgentDraftCompiler | None = None,
        catalog: CapabilityCatalog | None = None,
        *,
        catalogs: CapabilityCatalogService | None = None,
        publisher: AgentBundlePublisher | None = None,
        registry: AgentRegistry | None = None,
        knowledge: KnowledgeService | None = None,
        audit: AuditService | None = None,
        agent_ids: AgentIdentityProvider | None = None,
        draft_permissions: SharedDraftPermissionChecker | None = None,
        clock: Callable[[], datetime] | None = None,
        id_generator: Callable[[], str] | None = None,
    ) -> None:
        self._repository = repository
        self._compiler = compiler
        self._catalog = catalog
        self._catalogs = catalogs
        self._publisher = publisher
        self._registry = registry
        self._knowledge = knowledge
        self._audit = audit
        self._agent_ids = agent_ids
        self._draft_permissions = draft_permissions
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_generator = id_generator or (lambda: f"draft_{uuid4().hex}")

    async def capabilities(self, tenant_id: str, user_id: str) -> CapabilityCatalog:
        if self._catalogs is not None:
            return (await self._catalogs.get_for_user(tenant_id, user_id)).catalog
        if self._catalog is None:
            raise RuntimeError("Agent Studio capability catalog is not configured")
        return self._catalog

    async def _compiler_for(self, tenant_id: str, user_id: str) -> AgentDraftCompiler:
        if self._catalogs is not None:
            record = await self._catalogs.get_for_user(tenant_id, user_id)
            return AgentDraftCompiler(
                record.catalog,
                catalog_revision=record.revision,
            )
        if self._compiler is None:
            raise RuntimeError("Agent Studio compiler is not configured")
        return self._compiler

    async def _resolve_agent_id(self, tenant_id: str, user_id: str, name: str) -> str | None:
        """Stable Agent identity for a new personal draft, when configured."""
        if self._agent_ids is None:
            return None
        return await self._agent_ids.get_or_create_personal_agent_id(tenant_id, user_id, name)

    async def _available_generated_name(
        self,
        tenant_id: str,
        user_id: str,
        base_name: str,
    ) -> str:
        existing = {
            draft.spec.name for draft in await self._repository.list_for_user(tenant_id, user_id)
        }
        if base_name not in existing:
            return base_name
        suffix = 2
        while f"{base_name}-{suffix}" in existing:
            suffix += 1
        return f"{base_name}-{suffix}"

    async def create(
        self,
        *,
        tenant_id: str,
        user_id: str,
        request: CreateAgentDraftRequest,
    ) -> AgentDraft:
        now = self._clock()
        draft = AgentDraft(
            draftId=self._id_generator(),
            tenantId=tenant_id,
            revision=1,
            spec=create_draft_spec(
                name=request.name,
                domain=request.domain,
                display_name=request.display_name,
                description=request.description,
                template=request.template,
            ),
            createdBy=user_id,
            updatedBy=user_id,
            createdAt=now,
            updatedAt=now,
            agentId=await self._resolve_agent_id(tenant_id, user_id, request.name),
        )
        await self._repository.add(draft)
        return draft

    async def create_from_task(
        self,
        *,
        tenant_id: str,
        user_id: str,
        request: CreateTaskDrivenDraftRequest,
    ) -> TaskDrivenDraftResult:
        """Create a valid draft by compiling business intent against tenant capabilities."""

        now = self._clock()
        draft_id = self._id_generator()
        display_name = request.display_name or summarize_agent_display_name(request.task)
        name = request.name or await self._available_generated_name(
            tenant_id,
            user_id,
            summarize_agent_name(request.task, request.domain),
        )
        draft = AgentDraft(
            draftId=draft_id,
            tenantId=tenant_id,
            revision=1,
            spec=create_draft_spec(
                name=name,
                domain=request.domain,
                display_name=display_name,
                description=request.task.strip()[:500],
                template=AgentTemplate.ANALYST,
            ),
            createdBy=user_id,
            updatedBy=user_id,
            createdAt=now,
            updatedAt=now,
            agentId=await self._resolve_agent_id(tenant_id, user_id, name),
        )
        catalog = await self.capabilities(tenant_id, user_id)
        compiler = await self._compiler_for(tenant_id, user_id)
        draft, recommendation = configure_task_driven_draft(
            draft,
            request,
            catalog,
            compiler,
        )
        await self._repository.add(draft)
        return TaskDrivenDraftResult(draft=draft, recommendation=recommendation)

    async def create_internal_subagent(
        self,
        tenant_id: str,
        user_id: str,
        parent_id: str,
        request: CreateInternalSubagentRequest,
    ) -> CreatedInternalSubagent:
        parent = await self.get(tenant_id, user_id, parent_id)
        await self._require_shared_permission(tenant_id, user_id, parent, AgentPermission.EDIT)
        if parent.space_id is not None:
            raise ConflictError("请在个人智能体中创建内部子智能体；协作空间可引用已有智能体")
        if parent.parent_draft_id:
            raise ConflictError("子智能体不能继续创建子智能体")
        if parent.revision != request.expected_revision:
            raise ConflictError("父智能体已更新，请刷新后重试")
        if len(parent.spec.subagents) >= parent.spec.limits.max_subagents:
            raise ConflictError("已达到当前智能体的协作角色数量上限")
        drafts = await self._repository.list_for_user(tenant_id, user_id)
        if any(
            binding.ref.rsplit("@", 1)[0] == parent.spec.name
            for item in drafts
            for binding in item.spec.subagents
        ):
            raise ConflictError("当前智能体已被用作子智能体，不支持嵌套委派")
        now = self._clock()
        child_id = self._id_generator()
        alias = f"specialist-{uuid4().hex[:8]}"
        name = await self._available_generated_name(
            tenant_id,
            user_id,
            f"{parent.spec.name[:40]}-{alias}",
        )
        child = AgentDraft(
            draftId=child_id,
            tenantId=tenant_id,
            revision=1,
            parentDraftId=parent_id,
            spec=create_draft_spec(
                name=name,
                domain=parent.spec.domain,
                display_name=request.display_name.strip(),
                description=request.responsibility.strip(),
                template=AgentTemplate.ANALYST,
            ),
            createdBy=user_id,
            updatedBy=user_id,
            createdAt=now,
            updatedAt=now,
            agentId=await self._resolve_agent_id(tenant_id, user_id, name),
        )
        child, _ = configure_task_driven_draft(
            child,
            CreateTaskDrivenDraftRequest(
                task=request.responsibility,
                runtimePreference=parent.spec.runtime,
            ),
            await self.capabilities(tenant_id, user_id),
            await self._compiler_for(tenant_id, user_id),
        )
        child = child.model_copy(
            update={
                "spec": child.spec.model_copy(
                    update={
                        "runtime": parent.spec.runtime,
                        "model": parent.spec.model,
                    }
                )
            }
        )
        parent_spec = _auto_version_modified_release(
            parent,
            parent.spec.model_copy(
                update={
                    "subagents": (
                        *parent.spec.subagents,
                        DraftSubagent(
                            alias=alias,
                            ref=f"{name}@{child.spec.version}",
                            responsibility=request.responsibility.strip(),
                            background=True,
                        ),
                    ),
                    "builtin_tools": tuple(dict.fromkeys((*parent.spec.builtin_tools, "Task"))),
                    "permission_policy": "production-orchestrator"
                    if parent.spec.permission_policy == "production-read-only"
                    else parent.spec.permission_policy,
                }
            ),
        )
        updated = parent.model_copy(
            update={
                "spec": parent_spec,
                "revision": parent.revision + 1,
                "updated_by": user_id,
                "updated_at": now,
            }
        )
        await self._repository.add_child(parent.revision, updated, child)
        return CreatedInternalSubagent(parent=updated, child=child)

    async def set_placement(
        self,
        tenant_id: str,
        user_id: str,
        draft_id: str,
        request: AgentDraftPlacementRequest,
    ) -> AgentDraft:
        draft = await self.get(tenant_id, user_id, draft_id)
        await self._require_shared_permission(tenant_id, user_id, draft, AgentPermission.EDIT)
        if draft.space_id is not None:
            raise ConflictError("协作空间智能体保留独立入口")
        if request.parent_draft_id is not None:
            parent = await self.get(tenant_id, user_id, request.parent_draft_id)
            if parent.space_id or parent.parent_draft_id or parent.draft_id == draft_id:
                raise ConflictError("请选择个人主智能体作为归属")
            if draft.spec.subagents:
                raise ConflictError("包含子智能体的智能体不能转为内部子智能体")
            if not any(b.ref.rsplit("@", 1)[0] == draft.spec.name for b in parent.spec.subagents):
                raise ConflictError("请先在父智能体中引用该智能体")
            for other in await self._repository.list_for_user(tenant_id, user_id):
                if other.draft_id != parent.draft_id and any(
                    b.ref.rsplit("@", 1)[0] == draft.spec.name for b in other.spec.subagents
                ):
                    raise ConflictError("该智能体被多个主智能体引用，请保留独立入口")
        updated = draft.model_copy(
            update={
                "parent_draft_id": request.parent_draft_id,
                "revision": draft.revision + 1,
                "updated_at": self._clock(),
                "updated_by": user_id,
            }
        )
        await self._repository.replace(request.expected_revision, updated)
        return updated

    async def delete(
        self,
        *,
        tenant_id: str,
        user_id: str,
        draft_id: str,
        expected_revision: int,
    ) -> None:
        draft = await self._repository.get(tenant_id, user_id, draft_id)
        if draft.revision != expected_revision:
            raise ConflictError(
                "Agent draft revision changed: "
                f"expected={expected_revision} actual={draft.revision}"
            )
        if draft.space_id is not None:
            raise ConflictError("协作空间智能体不能从个人 Builder 删除")

        if any(
            item.parent_draft_id == draft_id
            for item in await self._repository.list_for_user(tenant_id, user_id)
        ):
            raise ConflictError("请先将内部子智能体转为独立智能体或删除，再删除父智能体")

        dependents = sorted(
            item.spec.display_name
            for item in await self._repository.list_for_user(tenant_id, user_id)
            if item.draft_id != draft_id
            and any(
                binding.ref.rsplit("@", 1)[0] == draft.spec.name for binding in item.spec.subagents
            )
        )
        if dependents:
            raise ConflictError(
                "智能体仍被其他草稿绑定为 Sub Agent，请先解除绑定：" + "、".join(dependents)
            )

        await self._repository.delete(
            tenant_id,
            user_id,
            draft_id,
            expected_revision,
        )
        if self._agent_ids is not None and draft.agent_id is not None:
            await self._agent_ids.archive_personal_agent(
                tenant_id,
                user_id,
                draft.agent_id,
                draft.spec.name,
            )
        if self._audit is not None:
            await self._audit.record(
                tenant_id=tenant_id,
                user_id=user_id,
                action="studio.draft.delete",
                resource_type="agent_draft",
                resource_id=draft_id,
                outcome="success",
                details={
                    "agent_id": draft.agent_id or "",
                    "name": draft.spec.name,
                    "published_version": draft.published_version or "",
                    "immutable_versions_preserved": True,
                },
            )

    async def _load_draft(self, tenant_id: str, user_id: str, draft_id: str) -> AgentDraft:
        """Owner-scoped draft, or a shared draft the user may view."""
        try:
            return await self._repository.get(tenant_id, user_id, draft_id)
        except NotFoundError:
            pass
        shared = await self._repository.get_shared(tenant_id, draft_id)
        if shared is None:
            raise NotFoundError(f"Agent draft not found: {draft_id}")
        await self._require_shared_permission(tenant_id, user_id, shared, AgentPermission.VIEW)
        return shared

    async def _require_shared_permission(
        self,
        tenant_id: str,
        user_id: str,
        draft: AgentDraft,
        permission: AgentPermission,
    ) -> None:
        if draft.space_id is None:
            return
        if self._draft_permissions is None:
            raise RuntimeError("shared draft permission checker is not configured")
        assert draft.agent_id is not None
        await self._draft_permissions.require_draft_permission(
            tenant_id,
            user_id,
            draft.space_id,
            draft.agent_id,
            permission,
        )

    async def get(self, tenant_id: str, owner_user_id: str, draft_id: str) -> AgentDraft:
        return await self._load_draft(tenant_id, owner_user_id, draft_id)

    async def list_workspace_drafts(
        self, tenant_id: str, user_id: str, space_id: str
    ) -> list[AgentDraftSummary]:
        """Drafts of every workspace Agent the member may view."""
        if self._draft_permissions is None:
            raise RuntimeError("shared draft permission checker is not configured")
        agents = await self._draft_permissions.list_agents(tenant_id, user_id, space_id)
        result: list[AgentDraftSummary] = []
        for agent in agents:
            draft = await self._repository.get_by_agent(tenant_id, agent.agent_id)
            if draft is not None:
                result.append(AgentDraftSummary.from_draft(draft))
        return result

    async def create_workspace_draft(
        self,
        *,
        tenant_id: str,
        user_id: str,
        space_id: str,
        agent_id: str,
        request: CreateAgentDraftRequest,
    ) -> AgentDraft:
        """Create the shared draft of a workspace Agent (EDIT required)."""
        if self._draft_permissions is None:
            raise RuntimeError("shared draft permission checker is not configured")
        await self._draft_permissions.require_draft_permission(
            tenant_id,
            user_id,
            space_id,
            agent_id,
            AgentPermission.EDIT,
        )
        existing = await self._repository.get_by_agent(tenant_id, agent_id)
        if existing is not None:
            raise ConflictError(f"workspace Agent already has a shared draft: {agent_id}")
        agent = await self._draft_permissions.get_space_agent(
            tenant_id, user_id, space_id, agent_id
        )
        if request.name != agent.name:
            raise ConflictError(
                f"shared draft name must match the workspace Agent identity: expected {agent.name}"
            )
        now = self._clock()
        draft = AgentDraft(
            draftId=self._id_generator(),
            tenantId=tenant_id,
            revision=1,
            spec=create_draft_spec(
                name=request.name,
                domain=request.domain,
                display_name=request.display_name,
                description=request.description,
                template=request.template,
            ),
            createdBy=user_id,
            updatedBy=user_id,
            createdAt=now,
            updatedAt=now,
            agentId=agent_id,
            spaceId=space_id,
        )
        await self._repository.add(draft)
        return draft

    async def ensure_workspace_draft_from_source(
        self,
        *,
        tenant_id: str,
        user_id: str,
        space_id: str,
        agent_id: str,
        source_owner_user_id: str,
        source_name: str,
        source_version: str,
    ) -> AgentDraft | None:
        """Clone the authoring draft when a personal release enters a space."""

        if self._draft_permissions is None:
            raise RuntimeError("shared draft permission checker is not configured")
        await self._draft_permissions.require_draft_permission(
            tenant_id,
            user_id,
            space_id,
            agent_id,
            AgentPermission.EDIT,
        )
        existing = await self._repository.get_by_agent(tenant_id, agent_id)
        if existing is not None:
            return existing

        candidates = [
            draft
            for draft in await self._repository.list_for_user(tenant_id, source_owner_user_id)
            if draft.space_id is None and draft.spec.name == source_name
        ]
        source = next(
            (draft for draft in candidates if draft.published_version == source_version),
            candidates[0] if candidates else None,
        )
        if source is None:
            return None

        now = self._clock()
        source_is_release = source.published_version == source_version
        shared = AgentDraft(
            draftId=self._id_generator(),
            tenantId=tenant_id,
            revision=1,
            spec=source.spec,
            createdBy=user_id,
            updatedBy=user_id,
            createdAt=now,
            updatedAt=now,
            agentId=agent_id,
            spaceId=space_id,
            publishedVersion=source_version,
            publishedHash=source.published_hash if source_is_release else None,
            publishedPackageHash=(source.published_package_hash if source_is_release else None),
        )
        await self._repository.add(shared)
        return shared

    async def list(self, tenant_id: str, owner_user_id: str) -> list[AgentDraftSummary]:
        return await self._repository.list_summaries(tenant_id, owner_user_id)

    async def replace(
        self,
        *,
        tenant_id: str,
        user_id: str,
        draft_id: str,
        request: ReplaceAgentDraftRequest,
    ) -> AgentDraft:
        current = await self._load_draft(tenant_id, user_id, draft_id)
        await self._require_shared_permission(tenant_id, user_id, current, AgentPermission.EDIT)
        if current.parent_draft_id and (
            request.spec.subagents or request.spec.name != current.spec.name
        ):
            raise ConflictError("内部子智能体不能嵌套委派或修改标识；可以修改显示名称")
        owned_drafts = await self._repository.list_for_user(tenant_id, user_id)
        for binding in request.spec.subagents:
            source = next(
                (item for item in owned_drafts if item.spec.name == binding.ref.rsplit("@", 1)[0]),
                None,
            )
            if source is not None and source.parent_draft_id not in {None, draft_id}:
                raise ConflictError("该子智能体属于其他主智能体，请先转为独立智能体再引用")
        if current.space_id is not None and request.spec.name != current.spec.name:
            raise ConflictError(
                "shared draft name cannot change; it is the workspace Agent identity"
            )
        current_skills = {skill.name: skill for skill in current.spec.skills}
        incoming_skill_names = {skill.name for skill in request.spec.skills}
        removed_platform_packages = {
            skill.source.package_id
            for skill in current.spec.skills
            if skill.name not in incoming_skill_names and skill.source is not None
        }
        merged_skills = tuple(
            _merge_editor_skill(current_skills.get(skill.name), skill)
            for skill in request.spec.skills
        )
        evaluation_cases = tuple(
            case
            for case in request.spec.evaluation_cases
            if not any(
                f"skill:{package_id}" in case.tags for package_id in removed_platform_packages
            )
        )
        candidate_spec = _auto_version_modified_release(
            current,
            request.spec.model_copy(
                update={
                    "skills": merged_skills,
                    "evaluation_cases": evaluation_cases,
                }
            ),
        )
        updated = current.model_copy(
            update={
                "revision": current.revision + 1,
                "spec": candidate_spec,
                "updated_by": user_id,
                "updated_at": self._clock(),
            }
        )
        await self._repository.replace(request.expected_revision, updated)
        return updated

    async def install_skill(
        self,
        *,
        tenant_id: str,
        user_id: str,
        draft_id: str,
        expected_revision: int,
        imported: ImportedSkill,
        evaluation_cases: tuple[EvalCase, ...] = (),
    ) -> AgentDraft:
        current = await self._load_draft(tenant_id, user_id, draft_id)
        await self._require_shared_permission(tenant_id, user_id, current, AgentPermission.EDIT)
        skills = tuple(
            imported.skill if skill.name == imported.skill.name else skill
            for skill in current.spec.skills
        )
        if all(skill.name != imported.skill.name for skill in current.spec.skills):
            skills = (*skills, imported.skill)
        cases_by_id = {case.id: case for case in current.spec.evaluation_cases}
        for case in evaluation_cases:
            cases_by_id[case.id] = case
        candidate_spec = _auto_version_modified_release(
            current,
            current.spec.model_copy(
                update={
                    "skills": skills,
                    "evaluation_cases": tuple(cases_by_id.values()),
                }
            ),
        )
        updated = current.model_copy(
            update={
                "revision": current.revision + 1,
                "spec": candidate_spec,
                "updated_by": user_id,
                "updated_at": self._clock(),
            }
        )
        await self._repository.replace(expected_revision, updated)
        return updated

    async def set_skill_references(
        self,
        *,
        tenant_id: str,
        user_id: str,
        draft_id: str,
        expected_revision: int,
        references: tuple[str, ...],
    ) -> AgentDraft:
        """Bind platform catalog Skills to a draft by package ID.

        Authorization lives at the API layer (catalog admin); this method keeps
        the shared-draft edit permission and optimistic-revision discipline.
        """

        current = await self._load_draft(tenant_id, user_id, draft_id)
        await self._require_shared_permission(tenant_id, user_id, current, AgentPermission.EDIT)
        ordered = tuple(dict.fromkeys(references))
        candidate_spec = _auto_version_modified_release(
            current,
            current.spec.model_copy(update={"skill_references": ordered}),
        )
        updated = current.model_copy(
            update={
                "revision": current.revision + 1,
                "spec": candidate_spec,
                "updated_by": user_id,
                "updated_at": self._clock(),
            }
        )
        await self._repository.replace(expected_revision, updated)
        return updated

    async def validate(
        self, tenant_id: str, owner_user_id: str, draft_id: str
    ) -> DraftValidationResult:
        compiler = await self._compiler_for(tenant_id, owner_user_id)
        draft = await self.get(tenant_id, owner_user_id, draft_id)
        validation = compiler.validate(draft)
        dependency_issues = await self._dependency_issues(tenant_id, owner_user_id, draft)
        if not dependency_issues:
            return validation
        return validation.model_copy(
            update={
                "ready": False,
                "issues": (*validation.issues, *dependency_issues),
            }
        )

    async def build_agent_patch(
        self,
        tenant_id: str,
        owner_user_id: str,
        draft_id: str,
        request: AgentBuilderPatchRequest,
    ) -> AgentBuilderPatch:
        draft = await self.get(tenant_id, owner_user_id, draft_id)
        if draft.revision != request.expected_revision:
            raise ConflictError(
                f"draft revision changed: expected {request.expected_revision}, "
                f"actual {draft.revision}"
            )
        compiler = await self._compiler_for(tenant_id, owner_user_id)
        return build_agent_patch(draft, request, compiler)

    async def converse_builder(
        self, tenant_id: str, user_id: str, draft_id: str,
        request: BuilderConversationRequest, models: ModelConfigurationService,
    ) -> BuilderConversationReply:
        current = await self.get(tenant_id, user_id, draft_id)
        await self._require_shared_permission(tenant_id, user_id, current, AgentPermission.EDIT)
        if current.revision != request.expected_revision:
            raise ConflictError("草稿已更新，请基于最新配置重新生成建议")
        # No credentials or script contents are passed to the authoring model.
        context = current.spec.model_dump(mode="json", by_alias=True)
        context["skills"] = [
            {"name": skill.name, "instructions": skill.instructions}
            for skill in current.spec.skills
        ]
        context.pop("pythonTools", None)
        prompt = json.dumps({
            "currentDraft": context,
            "conversation": [item.model_dump() for item in request.messages],
            "runContext": request.run_context,
        }, ensure_ascii=False)
        if len(prompt) > 180_000:
            raise ConflictError("当前草稿内容过长，请先在主编辑区选择具体 Skill 修改")
        reply = parse_builder_reply(await models.complete_text(
            tenant_id, current.spec.model.route_id,
            system_prompt=(
                BUILDER_AUTO_SYSTEM_PROMPT if request.intent == "auto" else BUILDER_SYSTEM_PROMPT
            ),
            user_prompt=prompt, max_tokens=12_000,
        ))
        if request.intent == "auto" and "action" not in reply.model_fields_set:
            raise ConflictError("模型未明确消息用途，请重试；未执行任何操作")
        if request.intent == "edit" and reply.action not in {"edit", "ask", "reply"}:
            raise ConflictError("修改模式不能发起试跑，请重新描述修改要求")
        candidate = apply_builder_changes(current.spec, reply.changes)
        await self._check_builder_candidate(tenant_id, user_id, current, candidate)
        latest = await self.get(tenant_id, user_id, draft_id)
        if latest.revision != current.revision:
            raise ConflictError("生成期间草稿已更新，请基于最新配置重新生成建议")
        before, after = current.spec.model_dump(by_alias=True), candidate.model_dump(by_alias=True)
        return BuilderConversationReply(
            reply=reply.reply, changes=reply.changes, action=reply.action, task=reply.task,
            baseRevision=current.revision,
            changedFields=tuple(
                name for name in after if after[name] != before[name]
            ),
        )

    async def apply_builder_edit(
        self, tenant_id: str, user_id: str, draft_id: str, request: BuilderApplyRequest,
    ) -> AgentDraft:
        current = await self.get(tenant_id, user_id, draft_id)
        await self._require_shared_permission(tenant_id, user_id, current, AgentPermission.EDIT)
        if current.revision != request.expected_revision:
            raise ConflictError("草稿已更新，本次建议未应用；请重新生成以免覆盖其他修改")
        candidate = apply_builder_changes(current.spec, request.changes)
        if candidate == current.spec:
            return current
        await self._check_builder_candidate(tenant_id, user_id, current, candidate)
        return await self.replace(
            tenant_id=tenant_id, user_id=user_id, draft_id=draft_id,
            request=ReplaceAgentDraftRequest(expectedRevision=current.revision, spec=candidate),
        )

    async def _check_builder_candidate(
        self, tenant_id: str, user_id: str, current: AgentDraft, spec: AgentDraftSpec,
    ) -> None:
        compiler = await self._compiler_for(tenant_id, user_id)
        previous = {(item.code, item.path) for item in compiler.validate(current).issues}
        validation = compiler.validate(current.model_copy(update={"spec": spec}))
        errors = [item.message for item in validation.issues
                  if item.severity == ValidationSeverity.ERROR
                  and (item.code, item.path) not in previous]
        if errors:
            raise ConflictError("修改未通过配置检查：" + "；".join(errors[:3]))

    async def bundle(self, tenant_id: str, owner_user_id: str, draft_id: str) -> CompiledAgentDraft:
        compiler = await self._compiler_for(tenant_id, owner_user_id)
        return compiler.compile(await self.get(tenant_id, owner_user_id, draft_id))

    async def preview_graph(
        self, tenant_id: str, owner_user_id: str, draft_id: str
    ) -> CompiledPreviewGraph:
        """Compile one immutable root-and-subagent graph for a Studio Try Run."""

        root = await self.get(tenant_id, owner_user_id, draft_id)
        compiler = await self._compiler_for(tenant_id, owner_user_id)
        drafts = await self._repository.list_for_user(tenant_id, owner_user_id)
        dependencies: list[PreviewGraphNode] = []
        dependency_by_draft: dict[str, PreviewGraphNode] = {}
        rewritten: list[DraftSubagent] = []
        for binding in root.spec.subagents:
            name, version = binding.ref.rsplit("@", 1)
            published = None
            if self._registry is not None:
                try:
                    candidate = await self._registry.get(tenant_id, owner_user_id, name, version)
                    if candidate.status is AgentVersionStatus.PUBLISHED:
                        published = candidate
                except NotFoundError:
                    pass
            if published is not None:
                rewritten.append(binding)
                continue
            source = next(
                (
                    item
                    for item in drafts
                    if item.draft_id != root.draft_id
                    and item.spec.name == name
                    and item.spec.version == version
                ),
                None,
            )
            if source is None:
                raise DraftCompilationError(
                    (
                        ValidationIssue(
                            code="subagent_preview_unavailable",
                            message=f"找不到可试跑的 Sub Agent 草稿或发布版本：{binding.ref}",
                            severity=ValidationSeverity.ERROR,
                            path="subagents",
                        ),
                    )
                )
            if source.spec.subagents:
                raise DraftCompilationError(
                    (
                        ValidationIssue(
                            code="nested_subagent_preview_unsupported",
                            message=f"Sub Agent 不能继续嵌套委派：{binding.ref}",
                            severity=ValidationSeverity.ERROR,
                            path="subagents",
                        ),
                    )
                )
            node = dependency_by_draft.get(source.draft_id)
            if node is None:
                compiled = compiler.compile(source)
                preview_version = (
                    f"preview-{source.draft_id}-{source.revision}-"
                    f"{compiled.report.snapshot.content_hash[:12]}"
                )
                node = PreviewGraphNode(
                    draft=source,
                    compiled=compiled,
                    preview_version=preview_version,
                )
                dependency_by_draft[source.draft_id] = node
                dependencies.append(node)
            rewritten.append(binding.model_copy(update={"ref": f"{name}@{node.preview_version}"}))
        preview_spec = root.spec.model_copy(update={"subagents": tuple(rewritten)})
        preview_root = root.model_copy(update={"spec": preview_spec})
        return CompiledPreviewGraph(
            root_draft=root,
            root=compiler.compile(preview_root),
            dependencies=tuple(dependencies),
        )

    async def publish_preview_dependencies(
        self,
        *,
        tenant_id: str,
        user_id: str,
        draft_id: str,
        preview_version: str,
    ) -> tuple[AgentVersion, ...]:
        """Publish the exact dependency graph proven by a successful Try Run."""

        graph = await self.preview_graph(tenant_id, user_id, draft_id)
        if self._registry is None:
            raise ConflictError("Agent Registry is unavailable for Preview verification")
        preview = await self._registry.get(
            tenant_id,
            user_id,
            graph.root_draft.spec.name,
            preview_version,
        )
        if graph.root.report.snapshot.content_hash != preview.manifest_hash:
            raise ConflictError(
                "Sub Agent graph changed after the verified Try Run; "
                "run it again before solidifying"
            )
        published: list[AgentVersion] = []
        for node in graph.dependencies:
            published.append(
                await self.publish(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    draft_id=node.draft.draft_id,
                    expected_revision=node.draft.revision,
                )
            )
        return tuple(published)

    async def nexau_bundle(
        self, tenant_id: str, owner_user_id: str, draft_id: str
    ) -> NexauAgentArchive:
        catalog = await self.capabilities(tenant_id, owner_user_id)
        return export_nexau_agent(
            await self.get(tenant_id, owner_user_id, draft_id),
            mcp_capabilities={item.reference: item for item in catalog.mcp_servers},
        )

    async def import_bundle(
        self,
        *,
        tenant_id: str,
        user_id: str,
        content: bytes,
    ) -> ImportedAgentBundle:
        parsed = parse_agent_bundle(content)
        now = self._clock()
        draft = AgentDraft(
            draftId=self._id_generator(),
            tenantId=tenant_id,
            revision=1,
            spec=parsed.spec,
            createdBy=user_id,
            updatedBy=user_id,
            createdAt=now,
            updatedAt=now,
            agentId=await self._resolve_agent_id(tenant_id, user_id, parsed.spec.name),
        )
        compiler = await self._compiler_for(tenant_id, user_id)
        round_trip_verified = False
        try:
            compiled = compiler.compile(draft)
            round_trip_verified = compiled.report.package_hash == parsed.package_hash
        except DraftCompilationError:
            if parsed.lossless:
                raise
        if parsed.lossless and not round_trip_verified:
            raise AgentBundleImportError(
                "Studio Bundle 无法按当前编译器无损重建；请检查能力目录或 Bundle 版本"
            )
        await self._repository.add(draft)
        if self._audit is not None:
            await self._audit.record(
                tenant_id=tenant_id,
                user_id=user_id,
                action="studio.draft.import",
                resource_type="agent_draft",
                resource_id=draft.draft_id,
                outcome="success",
                details={
                    "agent_name": draft.spec.name,
                    "agent_version": draft.spec.version,
                    "source_content_hash": parsed.content_hash,
                    "source_package_hash": parsed.package_hash,
                    "lossless": parsed.lossless,
                    "round_trip_verified": round_trip_verified,
                },
            )
        return ImportedAgentBundle(
            draft=draft,
            sourceContentHash=parsed.content_hash,
            sourcePackageHash=parsed.package_hash,
            lossless=parsed.lossless,
            roundTripVerified=round_trip_verified,
            warnings=parsed.warnings,
        )

    async def publish(
        self,
        *,
        tenant_id: str,
        user_id: str,
        draft_id: str,
        expected_revision: int | None = None,
    ) -> AgentVersion:
        if self._publisher is None:
            raise StudioPublisherNotConfiguredError("Agent Studio publisher is not configured")
        draft = await self._load_draft(tenant_id, user_id, draft_id)
        await self._require_shared_permission(tenant_id, user_id, draft, AgentPermission.PUBLISH)
        compiler = await self._compiler_for(tenant_id, user_id)
        try:
            if expected_revision is not None and draft.revision != expected_revision:
                raise ConflictError(
                    "Agent draft revision changed before publish: "
                    f"expected={expected_revision} actual={draft.revision}"
                )
            compiled = compiler.compile(draft)
            dependency_issues = await self._dependency_issues(tenant_id, user_id, draft)
            if dependency_issues:
                raise DraftCompilationError(dependency_issues)
            existing = await self._idempotent_version(tenant_id, user_id, draft, compiled)
            if existing is not None:
                await self._record_publish_audit(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    draft_id=draft_id,
                    draft_revision=draft.revision,
                    version=existing,
                    dependencies=tuple(sorted({item.ref for item in draft.spec.subagents})),
                    idempotent=True,
                )
                return existing
            try:
                version = await self._publisher.publish_bundle(
                    tenant_id,
                    user_id,
                    compiled.bundle,
                    agent_id=draft.agent_id,
                )
            except ConflictError as error:
                raise StudioPublicationConflictError(
                    f"Agent version content conflicts with existing immutable release: "
                    f"{draft.spec.name}@{draft.spec.version}"
                ) from error
            if draft.space_id is not None:
                # Shared draft publish releases the version into the space and
                # promotes it as the current version of the workspace Agent.
                if self._draft_permissions is None:
                    raise StudioPublisherNotConfiguredError(
                        "shared draft permission checker is not configured"
                    )
                await self._draft_permissions.share_release(
                    tenant_id,
                    user_id,
                    draft.space_id,
                    draft.spec.name,
                    version.version,
                )
        except Exception as error:
            await self._record_publish_failure(
                tenant_id=tenant_id,
                user_id=user_id,
                draft=draft,
                error=error,
            )
            raise
        published = draft.model_copy(
            update={
                "revision": draft.revision + 1,
                "updated_by": user_id,
                "updated_at": self._clock(),
                "published_version": version.version,
                "published_hash": version.manifest_hash,
                "published_package_hash": version.package_hash,
            }
        )
        try:
            await self._repository.replace(draft.revision, published)
        except Exception as error:
            await self._record_publish_failure(
                tenant_id=tenant_id,
                user_id=user_id,
                draft=draft,
                error=error,
            )
            raise
        await self._record_publish_audit(
            tenant_id=tenant_id,
            user_id=user_id,
            draft_id=draft_id,
            draft_revision=published.revision,
            version=version,
            dependencies=tuple(sorted({item.ref for item in draft.spec.subagents})),
            idempotent=False,
        )
        return version

    async def _dependency_issues(
        self, tenant_id: str, owner_user_id: str, draft: AgentDraft
    ) -> tuple[ValidationIssue, ...]:
        references = tuple(sorted({item.ref for item in draft.spec.subagents}))
        knowledge_references = tuple(sorted(set(draft.spec.knowledge_references)))
        issues: list[ValidationIssue] = []
        if knowledge_references:
            if self._knowledge is None:
                issues.append(
                    ValidationIssue(
                        code="knowledge_registry_unavailable",
                        message="无法复验知识库引用",
                        severity=ValidationSeverity.ERROR,
                        path="knowledgeReferences",
                    )
                )
            else:
                for reference in knowledge_references:
                    try:
                        await self._knowledge.get_base(tenant_id, reference)
                    except NotFoundError:
                        issues.append(
                            ValidationIssue(
                                code="knowledge_base_not_found",
                                message=f"知识库尚未注册：{reference}",
                                severity=ValidationSeverity.ERROR,
                                path="knowledgeReferences",
                            )
                        )
        if not references:
            return tuple(issues)
        if self._registry is None:
            issues.append(
                ValidationIssue(
                    code="subagent_registry_unavailable",
                    message="无法复验固定版本 Sub Agent",
                    severity=ValidationSeverity.ERROR,
                    path="subagents",
                )
            )
            return tuple(issues)
        tenant_drafts = await self._repository.list_for_user(tenant_id, owner_user_id)
        for reference in references:
            name, version_id = reference.rsplit("@", 1)
            try:
                version = await self._registry.get(tenant_id, owner_user_id, name, version_id)
            except NotFoundError:
                issues.append(
                    ValidationIssue(
                        code="subagent_not_published",
                        message=f"Sub Agent 尚未发布：{reference}",
                        severity=ValidationSeverity.ERROR,
                        path="subagents",
                    )
                )
                continue
            if version.status is not AgentVersionStatus.PUBLISHED:
                issues.append(
                    ValidationIssue(
                        code="subagent_not_published",
                        message=f"Sub Agent 不是已发布状态：{reference}",
                        severity=ValidationSeverity.ERROR,
                        path="subagents",
                    )
                )
                continue
            studio_source = next(
                (
                    item
                    for item in tenant_drafts
                    if item.spec.name == name and item.published_version == version_id
                ),
                None,
            )
            if studio_source is not None and (
                studio_source.published_hash != version.manifest_hash
                or studio_source.published_package_hash != version.package_hash
            ):
                issues.append(
                    ValidationIssue(
                        code="subagent_version_drift",
                        message=f"Sub Agent 发布哈希漂移：{reference}",
                        severity=ValidationSeverity.ERROR,
                        path="subagents",
                    )
                )
        return tuple(issues)

    async def _idempotent_version(
        self,
        tenant_id: str,
        owner_user_id: str,
        draft: AgentDraft,
        compiled: CompiledAgentDraft,
    ) -> AgentVersion | None:
        if (
            draft.published_version != draft.spec.version
            or draft.published_hash != compiled.report.snapshot.content_hash
            or draft.published_package_hash != compiled.report.package_hash
        ):
            return None
        if self._registry is None:
            raise StudioPublicationConflictError(
                "Published Draft cannot be verified against the Agent Registry"
            )
        try:
            existing = await self._registry.get(
                tenant_id, owner_user_id, draft.spec.name, draft.spec.version
            )
        except NotFoundError as error:
            raise StudioPublicationConflictError(
                "Draft publication metadata points to a missing Agent version"
            ) from error
        if (
            existing.status is not AgentVersionStatus.PUBLISHED
            or existing.manifest_hash != compiled.report.snapshot.content_hash
            or existing.package_hash != compiled.report.package_hash
        ):
            raise StudioPublicationConflictError(
                "Draft publication metadata differs from the immutable Agent version"
            )
        return existing

    async def _record_publish_audit(
        self,
        *,
        tenant_id: str,
        user_id: str,
        draft_id: str,
        draft_revision: int,
        version: AgentVersion,
        dependencies: tuple[str, ...],
        idempotent: bool,
    ) -> None:
        if self._audit is None:
            return
        await self._audit.record(
            tenant_id=tenant_id,
            user_id=user_id,
            action="studio.publish",
            resource_type="agent_draft",
            resource_id=draft_id,
            outcome="success",
            details={
                "name": version.name,
                "version": version.version,
                "manifest_hash": version.manifest_hash,
                "package_hash": version.package_hash or "",
                "draft_revision": draft_revision,
                "dependencies": list(dependencies),
                "idempotent": idempotent,
            },
        )

    async def _record_publish_failure(
        self,
        *,
        tenant_id: str,
        user_id: str,
        draft: AgentDraft,
        error: Exception,
    ) -> None:
        if self._audit is None:
            return
        if isinstance(error, DraftCompilationError):
            error_code = "draft_not_ready"
        elif isinstance(error, StudioPublicationConflictError):
            error_code = "version_conflict"
        elif isinstance(error, ConflictError):
            error_code = "draft_conflict"
        else:
            error_code = "publish_failed"
        try:
            await self._audit.record(
                tenant_id=tenant_id,
                user_id=user_id,
                action="studio.publish",
                resource_type="agent_draft",
                resource_id=draft.draft_id,
                outcome="denied",
                details={
                    "name": draft.spec.name,
                    "version": draft.spec.version,
                    "draft_revision": draft.revision,
                    "error_code": error_code,
                },
            )
        except Exception:
            # Preserve the authoritative publication failure. The request-level audit
            # middleware still records the denied HTTP result.
            pass
