import asyncio
from datetime import UTC, datetime, timedelta

from harness.core.errors import ConflictError, NotFoundError
from harness.studio.factory import create_draft_spec
from harness.studio.models import AgentDraft, AgentDraftSummary, AgentTemplate
from harness.studio.repositories import AgentDraftRepository

NOW = datetime(2026, 7, 16, tzinfo=UTC)


def draft(
    *,
    tenant_id: str = "tenant-a",
    draft_id: str = "draft-shared",
    name: str = "policy-researcher",
    updated_at: datetime = NOW,
) -> AgentDraft:
    return AgentDraft(
        draftId=draft_id,
        tenantId=tenant_id,
        revision=1,
        spec=create_draft_spec(
            name=name,
            domain="policy-research",
            display_name="政策研究助手",
            description="整理政策材料并输出有出处的研究结论。",
            template=AgentTemplate.ANALYST,
        ),
        createdBy="builder-a",
        updatedBy="builder-a",
        createdAt=NOW,
        updatedAt=updated_at,
    )


async def exercise_repository_contract(repository: AgentDraftRepository) -> None:
    tenant_a = draft()
    tenant_b = draft(tenant_id="tenant-b")
    newer = draft(
        draft_id="draft-newer",
        name="risk-reviewer",
        updated_at=NOW + timedelta(seconds=1),
    )

    await repository.add(tenant_a)
    await repository.add(tenant_b)
    await repository.add(newer)

    assert await repository.get("tenant-a", "builder-a", tenant_a.draft_id) == tenant_a
    assert await repository.get("tenant-b", "builder-a", tenant_b.draft_id) == tenant_b
    assert await repository.list_for_user("tenant-a", "builder-a") == [newer, tenant_a]
    assert await repository.list_for_user("tenant-b", "builder-a") == [tenant_b]
    assert await repository.list_for_user("tenant-c", "builder-a") == []
    assert await repository.list_summaries("tenant-a", "builder-a") == [
        AgentDraftSummary.from_draft(newer),
        AgentDraftSummary.from_draft(tenant_a),
    ]
    assert await repository.list_summaries("tenant-c", "builder-a") == []

    try:
        await repository.get("tenant-c", "builder-a", tenant_a.draft_id)
    except NotFoundError:
        pass
    else:
        raise AssertionError("cross-tenant get must not expose a draft")

    try:
        await repository.add(tenant_a)
    except ConflictError:
        pass
    else:
        raise AssertionError("duplicate tenant/draft key must conflict")

    invalid_increment = tenant_a.model_copy(update={"revision": 3})
    try:
        await repository.replace(1, invalid_increment)
    except ConflictError:
        pass
    else:
        raise AssertionError("replacement must increment the revision exactly once")

    updated = tenant_a.model_copy(
        update={
            "revision": 2,
            "updated_by": "builder-b",
            "updated_at": NOW + timedelta(seconds=2),
        }
    )
    await repository.replace(1, updated)
    assert await repository.get("tenant-a", "builder-a", tenant_a.draft_id) == updated
    assert await repository.get("tenant-b", "builder-a", tenant_b.draft_id) == tenant_b

    try:
        await repository.replace(1, updated)
    except ConflictError:
        pass
    else:
        raise AssertionError("stale revision must conflict")

    try:
        await repository.delete(
            "tenant-a", "builder-a", tenant_a.draft_id, expected_revision=1
        )
    except ConflictError:
        pass
    else:
        raise AssertionError("stale delete revision must conflict")

    await repository.delete(
        "tenant-a", "builder-a", tenant_a.draft_id, expected_revision=2
    )
    try:
        await repository.get("tenant-a", "builder-a", tenant_a.draft_id)
    except NotFoundError:
        pass
    else:
        raise AssertionError("deleted draft must not remain readable")
    assert await repository.get("tenant-b", "builder-a", tenant_b.draft_id) == tenant_b


async def exercise_concurrent_replace(repository: AgentDraftRepository) -> None:
    original = draft(draft_id="draft-concurrent")
    await repository.add(original)
    first = original.model_copy(
        update={
            "revision": 2,
            "updated_by": "builder-first",
            "updated_at": NOW + timedelta(seconds=1),
        }
    )
    second = original.model_copy(
        update={
            "revision": 2,
            "updated_by": "builder-second",
            "updated_at": NOW + timedelta(seconds=2),
        }
    )

    results = await asyncio.gather(
        repository.replace(1, first),
        repository.replace(1, second),
        return_exceptions=True,
    )

    assert sum(result is None for result in results) == 1
    conflicts = [result for result in results if isinstance(result, ConflictError)]
    assert len(conflicts) == 1
    stored = await repository.get("tenant-a", "builder-a", original.draft_id)
    assert stored in (first, second)
