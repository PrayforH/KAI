from datetime import UTC, datetime, timedelta

import pytest

from harness.adapters.memory import InMemoryAgentRegistry
from harness.application.agents import AgentService
from harness.auth.audit import AuditService
from harness.auth.repositories import InMemoryAuditRepository
from harness.core.errors import ConflictError, NotFoundError
from harness.core.manifest import AgentManifestSnapshot
from harness.core.models import AgentVersion
from harness.knowledge.models import (
    CreateKnowledgeBaseRequest,
    CreateKnowledgeSourceRequest,
)
from harness.knowledge.repositories import InMemoryKnowledgeRepository
from harness.knowledge.service import KnowledgeService
from harness.studio.catalog import default_capability_catalog
from harness.studio.compiler import AgentDraftCompiler, DraftCompilationError
from harness.studio.models import (
    AgentTemplate,
    CreateAgentDraftRequest,
    DraftSubagent,
    ReplaceAgentDraftRequest,
)
from harness.studio.platform_skills import imported_platform_skill, platform_skill_package
from harness.studio.repositories import InMemoryAgentDraftRepository
from harness.studio.service import AgentStudioService

NOW = datetime(2026, 7, 16, tzinfo=UTC)


def create_request(
    *,
    name: str = "contract-reviewer",
    template: AgentTemplate = AgentTemplate.ANALYST,
) -> CreateAgentDraftRequest:
    return CreateAgentDraftRequest(
        name=name,
        domain="contract-review",
        displayName="合同审查助手",
        description="检查上传合同并生成有出处的风险清单。",
        template=template,
    )


def studio(
    *,
    publisher: AgentService | None = None,
    registry: InMemoryAgentRegistry | None = None,
    repository: InMemoryAgentDraftRepository | None = None,
    audit: AuditService | None = None,
    knowledge: KnowledgeService | None = None,
) -> AgentStudioService:
    catalog = default_capability_catalog()
    return AgentStudioService(
        repository or InMemoryAgentDraftRepository(),
        AgentDraftCompiler(catalog),
        catalog,
        publisher=publisher,
        registry=registry,
        knowledge=knowledge,
        audit=audit,
        clock=lambda: NOW,
        id_generator=lambda: "draft_contract",
    )


@pytest.mark.asyncio
async def test_drafts_are_tenant_scoped_and_return_summary_order() -> None:
    service = studio()
    created = await service.create(
        tenant_id="tenant-a", user_id="builder", request=create_request()
    )

    assert (await service.list("tenant-a", "builder"))[0].draft_id == created.draft_id
    assert await service.list("tenant-b", "builder") == []
    with pytest.raises(NotFoundError):
        await service.get("tenant-b", "builder", created.draft_id)


@pytest.mark.asyncio
async def test_replace_uses_optimistic_revision_and_preserves_publication_identity() -> None:
    service = studio()
    created = await service.create(
        tenant_id="tenant-a", user_id="builder", request=create_request()
    )
    changed_spec = created.spec.model_copy(update={"description": "更新后的合同审查说明。"})
    request = ReplaceAgentDraftRequest(expectedRevision=1, spec=changed_spec)

    updated = await service.replace(
        tenant_id="tenant-a",
        user_id="builder",
        draft_id=created.draft_id,
        request=request,
    )

    assert updated.revision == 2
    assert updated.updated_by == "builder"
    with pytest.raises(ConflictError, match="revision changed"):
        await service.replace(
            tenant_id="tenant-a",
            user_id="builder",
            draft_id=created.draft_id,
            request=request,
        )


@pytest.mark.asyncio
async def test_editing_an_imported_platform_skill_marks_package_provenance_modified() -> None:
    service = studio()
    created = await service.create(
        tenant_id="tenant-a", user_id="builder", request=create_request()
    )
    package = platform_skill_package("evidence-reporting", 1)
    installed = await service.install_skill(
        tenant_id="tenant-a",
        user_id="builder",
        draft_id=created.draft_id,
        expected_revision=created.revision,
        imported=imported_platform_skill(package),
        evaluation_cases=package.evaluation_cases,
    )
    skill = installed.spec.skills[0].model_copy(update={"description": "租户定制报告规范。"})
    updated = await service.replace(
        tenant_id="tenant-a",
        user_id="builder",
        draft_id=created.draft_id,
        request=ReplaceAgentDraftRequest(
            expectedRevision=installed.revision,
            spec=installed.spec.model_copy(update={"skills": (skill,)}),
        ),
    )

    assert installed.spec.skills[0].source is not None
    assert any(
        "skill:evidence-reporting" in case.tags
        for case in installed.spec.evaluation_cases
    )
    assert updated.spec.skills[0].source is not None
    assert updated.spec.skills[0].source.modified is True

    uninstalled = await service.replace(
        tenant_id="tenant-a",
        user_id="builder",
        draft_id=created.draft_id,
        request=ReplaceAgentDraftRequest(
            expectedRevision=updated.revision,
            spec=updated.spec.model_copy(update={"skills": ()}),
        ),
    )

    assert not any(
        "skill:evidence-reporting" in case.tags
        for case in uninstalled.spec.evaluation_cases
    )


@pytest.mark.asyncio
async def test_publish_reuses_production_bundle_gate_and_marks_draft() -> None:
    registry = InMemoryAgentRegistry()
    publisher = AgentService(
        registry,
        clock=lambda: NOW + timedelta(seconds=1),
        environment="production",
    )
    service = studio(publisher=publisher, registry=registry)
    created = await service.create(
        tenant_id="tenant-a", user_id="builder", request=create_request()
    )

    version = await service.publish(
        tenant_id="tenant-a", user_id="builder", draft_id=created.draft_id
    )
    stored = await service.get("tenant-a", "builder", created.draft_id)

    assert version.name == "contract-reviewer"
    assert version.version == "0.1.0"
    assert version.package_hash is not None
    assert stored.published_version == "0.1.0"
    assert stored.published_hash == version.manifest_hash
    assert stored.published_package_hash == version.package_hash
    assert stored.updated_by == "builder"
    assert stored.revision == 2


@pytest.mark.asyncio
async def test_exact_studio_publish_retry_is_idempotent_and_audited() -> None:
    registry = InMemoryAgentRegistry()
    publisher = AgentService(
        registry,
        clock=lambda: NOW + timedelta(seconds=1),
        environment="production",
    )
    audit_repository = InMemoryAuditRepository()
    service = studio(
        publisher=publisher,
        registry=registry,
        audit=AuditService(audit_repository),
    )
    created = await service.create(
        tenant_id="tenant-a", user_id="builder", request=create_request()
    )

    first = await service.publish(
        tenant_id="tenant-a",
        user_id="builder",
        draft_id=created.draft_id,
        expected_revision=1,
    )
    repeated = await service.publish(
        tenant_id="tenant-a",
        user_id="builder",
        draft_id=created.draft_id,
        expected_revision=2,
    )

    assert repeated == first
    assert (await service.get("tenant-a", "builder", created.draft_id)).revision == 2
    audits = await audit_repository.list_for_tenant("tenant-a", limit=10)
    assert [entry.action for entry in audits] == ["studio.publish", "studio.publish"]
    assert {entry.details["idempotent"] for entry in audits} == {False, True}


@pytest.mark.asyncio
async def test_unpublished_subagent_blocks_validation_and_publication() -> None:
    registry = InMemoryAgentRegistry()
    publisher = AgentService(registry, clock=lambda: NOW, environment="production")
    service = studio(publisher=publisher, registry=registry)
    created = await service.create(
        tenant_id="tenant-a",
        user_id="builder",
        request=create_request(template=AgentTemplate.ORCHESTRATOR),
    )
    created = await service.replace(
        tenant_id="tenant-a",
        user_id="builder",
        draft_id=created.draft_id,
        request=ReplaceAgentDraftRequest(
            expectedRevision=created.revision,
            spec=created.spec.model_copy(
                update={
                    "builtin_tools": (*created.spec.builtin_tools, "Task"),
                    "subagents": (
                        DraftSubagent(
                            alias="risk-reviewer",
                            ref="helper-agent@1.0.0",
                            responsibility="独立复核风险结论。",
                        ),
                    ),
                }
            ),
        ),
    )

    validation = await service.validate("tenant-a", "builder", created.draft_id)

    assert validation.ready is False
    assert {issue.code for issue in validation.issues} >= {"subagent_not_published"}
    with pytest.raises(DraftCompilationError, match="尚未发布"):
        await service.publish(
            tenant_id="tenant-a",
            user_id="builder",
            draft_id=created.draft_id,
        )


@pytest.mark.asyncio
async def test_unknown_knowledge_reference_blocks_validation_and_publication() -> None:
    registry = InMemoryAgentRegistry()
    publisher = AgentService(registry, clock=lambda: NOW, environment="production")
    knowledge = KnowledgeService(InMemoryKnowledgeRepository())
    service = studio(
        publisher=publisher,
        registry=registry,
        knowledge=knowledge,
    )
    created = await service.create(
        tenant_id="tenant-a",
        user_id="builder",
        request=create_request(),
    )
    updated = await service.replace(
        tenant_id="tenant-a",
        user_id="builder",
        draft_id=created.draft_id,
        request=ReplaceAgentDraftRequest(
            expectedRevision=created.revision,
            spec=created.spec.model_copy(update={"knowledge_references": ("missing-policy",)}),
        ),
    )

    validation = await service.validate("tenant-a", "builder", updated.draft_id)

    assert validation.ready is False
    assert "knowledge_base_not_found" in {issue.code for issue in validation.issues}
    with pytest.raises(DraftCompilationError, match="知识库尚未注册"):
        await service.publish(
            tenant_id="tenant-a",
            user_id="builder",
            draft_id=updated.draft_id,
        )


@pytest.mark.asyncio
async def test_registered_knowledge_reference_can_be_published() -> None:
    registry = InMemoryAgentRegistry()
    publisher = AgentService(registry, clock=lambda: NOW, environment="production")
    knowledge = KnowledgeService(InMemoryKnowledgeRepository())
    await knowledge.create_source(
        "tenant-a",
        "builder",
        CreateKnowledgeSourceRequest.model_validate(
            {
                "reference": "handbook",
                "displayName": "Handbook",
                "kind": "file",
                "config": {
                    "type": "file",
                    "documents": [
                        {
                            "documentId": "leave",
                            "title": "Leave",
                            "content": "Annual leave is 15 days.",
                        }
                    ],
                },
            }
        ),
    )
    await knowledge.create_base(
        "tenant-a",
        "builder",
        CreateKnowledgeBaseRequest(
            reference="company-policy",
            displayName="Company policy",
            sourceReferences=("handbook",),
        ),
    )
    service = studio(
        publisher=publisher,
        registry=registry,
        knowledge=knowledge,
    )
    created = await service.create(
        tenant_id="tenant-a",
        user_id="builder",
        request=create_request(),
    )
    updated = await service.replace(
        tenant_id="tenant-a",
        user_id="builder",
        draft_id=created.draft_id,
        request=ReplaceAgentDraftRequest(
            expectedRevision=created.revision,
            spec=created.spec.model_copy(update={"knowledge_references": ("company-policy",)}),
        ),
    )

    version = await service.publish(
        tenant_id="tenant-a",
        user_id="builder",
        draft_id=updated.draft_id,
    )

    snapshot = AgentManifestSnapshot.model_validate(version.snapshot)
    assert snapshot.manifest.spec.knowledge_references == ("company-policy",)


class DriftRegistry:
    def __init__(self, delegate: InMemoryAgentRegistry) -> None:
        self._delegate = delegate

    async def add(self, version: AgentVersion) -> None:
        await self._delegate.add(version)

    async def get(
        self, tenant_id: str, owner_user_id: str, name: str, version: str
    ) -> AgentVersion:
        stored = await self._delegate.get(tenant_id, owner_user_id, name, version)
        if name == "helper-agent":
            return stored.model_copy(update={"manifest_hash": "f" * 64})
        return stored

    async def list_for_user(self, tenant_id: str, owner_user_id: str) -> list[AgentVersion]:
        return await self._delegate.list_for_user(tenant_id, owner_user_id)

    async def list_catalog_for_user(
        self, tenant_id: str, owner_user_id: str
    ) -> list[AgentVersion]:
        return await self._delegate.list_catalog_for_user(tenant_id, owner_user_id)

    async def move_owner(
        self,
        tenant_id: str,
        from_user_id: str,
        to_user_id: str,
        name: str,
    ) -> int:
        return await self._delegate.move_owner(tenant_id, from_user_id, to_user_id, name)


@pytest.mark.asyncio
async def test_subagent_hash_drift_blocks_lead_publication() -> None:
    registry = InMemoryAgentRegistry()
    publisher = AgentService(registry, clock=lambda: NOW, environment="production")
    repository = InMemoryAgentDraftRepository()
    child_service = AgentStudioService(
        repository,
        AgentDraftCompiler(default_capability_catalog()),
        default_capability_catalog(),
        publisher=publisher,
        registry=registry,
        clock=lambda: NOW,
        id_generator=lambda: "draft_helper",
    )
    child = await child_service.create(
        tenant_id="tenant-a",
        user_id="builder",
        request=create_request(name="helper-agent"),
    )
    child = await child_service.replace(
        tenant_id="tenant-a",
        user_id="builder",
        draft_id=child.draft_id,
        request=ReplaceAgentDraftRequest(
            expectedRevision=child.revision,
            spec=child.spec.model_copy(update={"version": "1.0.0"}),
        ),
    )
    await child_service.publish(tenant_id="tenant-a", user_id="builder", draft_id=child.draft_id)
    lead_service = AgentStudioService(
        repository,
        AgentDraftCompiler(default_capability_catalog()),
        default_capability_catalog(),
        publisher=publisher,
        registry=DriftRegistry(registry),
        clock=lambda: NOW,
        id_generator=lambda: "draft_lead",
    )
    lead = await lead_service.create(
        tenant_id="tenant-a",
        user_id="builder",
        request=create_request(name="contract-lead", template=AgentTemplate.ORCHESTRATOR),
    )
    lead = await lead_service.replace(
        tenant_id="tenant-a",
        user_id="builder",
        draft_id=lead.draft_id,
        request=ReplaceAgentDraftRequest(
            expectedRevision=lead.revision,
            spec=lead.spec.model_copy(
                update={
                    "builtin_tools": (*lead.spec.builtin_tools, "Task"),
                    "subagents": (
                        DraftSubagent(
                            alias="risk-reviewer",
                            ref="helper-agent@1.0.0",
                            responsibility="独立复核风险结论。",
                        ),
                    ),
                }
            ),
        ),
    )

    validation = await lead_service.validate("tenant-a", "builder", lead.draft_id)

    assert validation.ready is False
    assert {issue.code for issue in validation.issues} >= {"subagent_version_drift"}
