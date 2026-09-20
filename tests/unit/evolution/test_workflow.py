from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from harness.api.app import create_app
from harness.api.dependencies import ApiContainer, Identity, build_memory_container
from harness.core.errors import ConflictError, NotFoundError
from harness.evals.models import CreateEvalDatasetVersionRequest
from harness.evals.suite import EvalCase, EvalExpectation
from harness.evolution.api import human
from harness.evolution.models import (
    CreateEvolutionJob,
    DatasetRef,
    EvolutionJob,
    ExperienceRequest,
    Patch,
    ProposeCandidate,
    ReviewRequest,
)
from harness.runtime.base import RuntimeContext, RuntimeEvent
from harness.runtime.fake import FakeRuntime
from harness.studio.models import (
    AgentDraft,
    AgentTemplate,
    CreateAgentDraftRequest,
    DraftLimits,
    ReplaceAgentDraftRequest,
)


async def seed(container: ApiContainer) -> tuple[AgentDraft, CreateEvolutionJob, EvolutionJob]:
    draft = await container.studio.create(
        tenant_id="t",
        user_id="u",
        request=CreateAgentDraftRequest(
            name="evolving-agent",
            displayName="演进测试",
            domain="test",
            description="隔离工程验收智能体",
            template=AgentTemplate.ANALYST,
        ),
    )
    draft = await container.studio.replace(
        tenant_id="t",
        user_id="u",
        draft_id=draft.draft_id,
        request=ReplaceAgentDraftRequest(
            expectedRevision=draft.revision,
            spec=draft.spec.model_copy(
                update={
                    "system_prompt": draft.spec.system_prompt
                    + "\nReturn OLD as the complete answer.",
                    "limits": DraftLimits(maxBudgetUsd=0.01, timeoutSeconds=30),
                    "evaluation_cases": (
                        EvalCase(
                            id="answer",
                            prompt="answer",
                            tags=("happy", "ambiguous", "safety"),
                            expect=EvalExpectation(outputContains=("FIXED",), maxCostUsd=0.01),
                        ),
                    ),
                }
            ),
        ),
    )
    await container.studio.publish(tenant_id="t", user_id="u", draft_id=draft.draft_id)
    draft = await container.studio.get("t", "u", draft.draft_id)
    ds = await container.evals.create_dataset_version(
        tenant_id="t",
        user_id="u",
        request=CreateEvalDatasetVersionRequest(
            draftId=draft.draft_id, expectedRevision=draft.revision, name="工程验证集"
        ),
    )
    request = CreateEvolutionJob(
        draftId=draft.draft_id,
        expectedRevision=draft.revision,
        objective="修复确定性工程用例中的输出错误",
        dataset=DatasetRef(datasetId=ds.dataset_id, version=ds.version),
        allowedTargets=("systemPrompt",),
        idempotencyKey="one",
    )
    job = await container.evolution.create("t", "u", request)
    return draft, request, job


async def propose(c: ApiContainer, job: EvolutionJob) -> EvolutionJob:
    return await c.evolution.propose(
        "t",
        "u",
        job.job_id,
        ProposeCandidate(
            expectedRevision=job.revision,
            patches=(
                Patch(target="systemPrompt", oldText="OLD", newText="FIXED", reason="修正输出约束"),
            ),
            rationale="修复输出断言",
        ),
    )


async def evaluate(c: ApiContainer, job: EvolutionJob) -> EvolutionJob:
    job = await c.evolution.evaluate(
        "t", "u", job.job_id, job.candidates[0].candidate_id, job.revision
    )
    for _ in range(60):
        await c.eval_controller.process_once()
        task = await c.task_queue.dequeue()
        if task:
            await c.worker.execute(task.tenant_id, task.run_id)
            await c.task_queue.acknowledge(task)
        await c.evolution.reconcile_pending()
        job = await c.evolution.get("t", "u", job.job_id)
        if job.candidates[0].status != "evaluating":
            return job
    raise AssertionError("Paired evaluation did not converge")


@pytest.fixture
def measured_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    async def execute(self: FakeRuntime, context: RuntimeContext) -> AsyncIterator[RuntimeEvent]:
        # Engineering fixture: deterministic result keyed by isolated preview role.
        text = "FIXED" if "evo-preview-" in context.session.agent_version else "OLD"
        yield RuntimeEvent(type="message.delta", payload={"text": text})
        yield RuntimeEvent(type="message.completed", payload={"text": text, "role": "assistant"})
        yield RuntimeEvent(
            type="runtime.result",
            payload={"total_cost_usd": 0.001, "usage": {"input_tokens": 10, "output_tokens": 2}},
        )

    monkeypatch.setattr(FakeRuntime, "execute", execute)


@pytest.mark.asyncio
async def test_real_controller_path_to_review_release_rollback(measured_runtime: None) -> None:
    c = build_memory_container()
    draft, request, job = await seed(c)
    assert (await c.evolution.create("t", "u", request)) == job
    job = await propose(c, job)
    candidate = job.candidates[0]
    compiled = await c.studio.compile_frozen("t", "u", draft.draft_id, candidate.spec)
    with pytest.raises(ConflictError):
        await c.agents.publish_bundle("t", "u", compiled.bundle)
    job = await evaluate(c, job)
    candidate = job.candidates[0]
    assert candidate.comparison is not None
    assert candidate.comparison.status == "passed"
    assert candidate.comparison.improved == ("trial-1:answer",)
    assert (await c.studio.get("t", "u", draft.draft_id)).spec == draft.spec
    published = await c.agents.list_published("t", "u")
    assert [v.version for v in published] == [draft.spec.version]
    runs = await c.evals.list_runs("t", "u")
    assert len(runs) == 2 and all(v.run.preview_execution for v in runs)
    assert runs[0].cases[0].session_id != runs[1].cases[0].session_id
    with pytest.raises(ConflictError, match="hash"):
        await c.evolution.review(
            "t",
            "u",
            job.job_id,
            candidate.candidate_id,
            ReviewRequest(
                expectedRevision=job.revision,
                decision="approve",
                reason="证据已检查通过",
                reportHash="0" * 64,
            ),
        )
    job = await c.evolution.review(
        "t",
        "u",
        job.job_id,
        candidate.candidate_id,
        ReviewRequest(
            expectedRevision=job.revision,
            decision="approve",
            reason="工程证据已检查通过",
            reportHash=candidate.comparison.report_hash,
        ),
    )
    job = await c.evolution.release("t", "u", job.job_id, candidate.candidate_id, job.revision)
    assert job.candidates[0].status == "released"
    job = await c.evolution.observe("t", "u", job.job_id, candidate.candidate_id, job.revision)
    assert job.observations[-1].total_runs == 0
    assert "暂无" in job.observations[-1].conclusion
    gate = await c.evals.gate("t", "u", job.agent_name, candidate.spec.version)
    assert gate.passed  # The gate binds both immutable hashes, not a preview version alias.
    job = await c.evolution.add_experience(
        "t",
        "u",
        job.job_id,
        ExperienceRequest(
            expectedRevision=job.revision,
            candidateId=candidate.candidate_id,
            kind="evolution_lesson",
            content="明确输出约束可以修复该工程用例。",
            conditions="仅限本固定验证集",
        ),
    )
    job = await c.evolution.set_experience(
        "t", "u", job.job_id, job.experiences[0].experience_id, job.revision, "reviewed"
    )
    assert job.experiences[0].status == "reviewed"
    job = await c.evolution.rollback("t", "u", job.job_id, candidate.candidate_id, job.revision)
    assert job.candidates[0].status == "rolled_back"
    with pytest.raises(ConflictError):
        await c.agents.publish_bundle("t", "u", compiled.bundle)


@pytest.mark.asyncio
async def test_unknown_cost_blocks_approval_and_owner_isolation() -> None:
    c = build_memory_container()
    _, request, job = await seed(c)
    with pytest.raises(NotFoundError):
        await c.evolution.get("other", "u", job.job_id)
    with pytest.raises(NotFoundError):
        await c.evolution.get("t", "other", job.job_id)
    with pytest.raises(ConflictError):
        await c.evolution.create(
            "t", "u", request.model_copy(update={"objective": "another objective"})
        )
    job = await propose(c, job)
    with pytest.raises(ConflictError, match="revision"):
        await propose(c, job.model_copy(update={"revision": 1}))
    job = await evaluate(c, job)
    candidate = job.candidates[0]
    assert candidate.comparison is not None
    assert candidate.comparison.status == "insufficient_evidence"
    assert candidate.comparison.unknown_cost_count == 2
    with pytest.raises(ConflictError, match="insufficient"):
        await c.evolution.review(
            "t",
            "u",
            job.job_id,
            candidate.candidate_id,
            ReviewRequest(
                expectedRevision=job.revision,
                decision="approve",
                reason="不得无证据批准",
                reportHash=candidate.comparison.report_hash,
            ),
        )


@pytest.mark.asyncio
async def test_patch_limits_cancel_and_expiry() -> None:
    c = build_memory_container()
    _, _, job = await seed(c)
    with pytest.raises(ConflictError, match="allowlist"):
        await c.evolution.propose(
            "t",
            "u",
            job.job_id,
            ProposeCandidate(
                expectedRevision=job.revision,
                patches=(
                    Patch(target="skill:other", oldText="OLD", newText="FIXED", reason="bad"),
                ),
                rationale="bad",
            ),
        )
    job = await propose(c, job)
    job = await c.evolution.evaluate(
        "t", "u", job.job_id, job.candidates[0].candidate_id, job.revision
    )
    count = len(await c.evals.list_runs("t", "u"))
    job = await c.evolution.refresh("t", "u", job.job_id, job.revision)
    assert len(await c.evals.list_runs("t", "u")) == count == 2
    expired = job.model_copy(
        update={
            "revision": job.revision + 1,
            "expires_at": datetime.now(UTC) - timedelta(seconds=1),
        }
    )
    await c.evolution.repository.replace(job.revision, expired)
    await c.evolution.reconcile_pending()
    stopped = await c.evolution.get("t", "u", job.job_id)
    assert stopped.status == "budget_exhausted"
    assert all(
        v.run.status.value in {"cancelling", "cancelled"} for v in await c.evals.list_runs("t", "u")
    )


def test_service_identity_cannot_approve() -> None:
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as failure:
        human(
            Identity(
                tenant_id="t",
                user_id="u",
                roles=frozenset({"owner"}),
                authentication_method="service",
            )
        )
    assert failure.value.status_code == 403
    human(
        Identity(
            tenant_id="t", user_id="u", roles=frozenset({"owner"}), authentication_method="jwt"
        )
    )


@pytest.mark.asyncio
async def test_evolution_routes_require_scope_and_hide_other_owners() -> None:
    c = build_memory_container()
    _, _, job = await seed(c)
    app = create_app(c)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        headers = {"X-Tenant-Id": "t", "X-User-Id": "other"}
        response = await client.get(f"/v1/studio/evolution/{job.job_id}", headers=headers)
        assert response.status_code == 404
        response = await client.post(
            f"/v1/studio/evolution/{job.job_id}/candidates/no/review",
            headers={"X-Tenant-Id": "t", "X-User-Id": "u"},
            json={
                "expectedRevision": 1,
                "decision": "approve",
                "reason": "test approval",
                "reportHash": "0" * 64,
            },
        )
        assert response.status_code == 403
