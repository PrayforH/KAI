"""Bounded, evidence-driven improvements; the optimizer has no publication authority."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from harness.application.agents import AgentService
from harness.core.errors import ConflictError, NotFoundError
from harness.core.manifest import AgentManifestSnapshot
from harness.evals.models import CreateEvalRunRequest, EvalRunView
from harness.evals.service import EvalControlPlaneService
from harness.evolution.models import (
    Candidate,
    Comparison,
    CreateEvolutionJob,
    Evidence,
    EvolutionJob,
    Experience,
    ExperienceRequest,
    HistoryEntry,
    Observation,
    ProposeCandidate,
    Review,
    ReviewRequest,
    Trial,
)
from harness.evolution.repositories import EvolutionRepository
from harness.quality.service import QualityService
from harness.studio.models import AgentDraftSpec
from harness.studio.service import AgentStudioService


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def _data(value: Any) -> Any:
    return value.model_dump(mode="json", by_alias=True)


class EvolutionService:
    def __init__(
        self,
        repository: EvolutionRepository,
        studio: AgentStudioService,
        evals: EvalControlPlaneService,
        agents: AgentService,
        quality: QualityService | None = None,
    ) -> None:
        self.repository, self.studio, self.evals, self.agents = repository, studio, evals, agents
        self.quality = quality

    async def reconcile_pending(self) -> int:
        count = 0
        for job in await self.repository.pending():
            try:
                updated = await self.refresh(job.tenant_id, job.owner_id, job.job_id, job.revision)
                count += updated.revision != job.revision
            except (ConflictError, NotFoundError):
                # Another worker won the CAS, or frozen authoring inputs changed.
                continue
        return count

    async def get(self, tenant: str, owner: str, job_id: str) -> EvolutionJob:
        return await self.repository.get(tenant, owner, job_id)

    async def list(self, tenant: str, owner: str) -> list[EvolutionJob]:
        return await self.repository.list(tenant, owner)

    async def _policy(self, tenant: str, owner: str) -> str:
        return digest(_data(await self.studio.capabilities(tenant, owner)))

    async def create(self, tenant: str, owner: str, request: CreateEvolutionJob) -> EvolutionJob:
        job_id = "evo_" + digest([tenant, owner, request.idempotency_key])[:32]
        request_hash = digest(_data(request))
        try:
            old = await self.get(tenant, owner, job_id)
        except NotFoundError:
            old = None
        if old is not None:
            if old.request_hash != request_hash:
                raise ConflictError("Idempotency key was reused with different inputs")
            return old
        draft = await self.studio.get(tenant, owner, request.draft_id)
        if draft.space_id is not None or draft.created_by != owner:
            raise ConflictError("Evolution currently supports personal Agent drafts only")
        if draft.revision != request.expected_revision:
            raise ConflictError("Draft revision changed")
        compiled = await self.studio.bundle(tenant, owner, draft.draft_id)
        published = await self.agents.get_published(
            tenant, owner, draft.spec.name, draft.spec.version
        )
        if (published.manifest_hash, published.package_hash) != (
            compiled.report.snapshot.content_hash,
            compiled.report.package_hash,
        ):
            raise ConflictError("Publish the exact baseline before starting evolution")
        dataset = await self.evals.get_dataset(
            tenant, owner, request.dataset.dataset_id, request.dataset.version
        )
        if dataset.agent_name != draft.spec.name or dataset.split != "validation":
            raise ConflictError("Choose a validation dataset belonging to this Agent")
        if len({case.id for case in dataset.cases}) != len(dataset.cases):
            raise ConflictError("Dataset case IDs must be unique")
        if len(dataset.cases) > request.budget.max_cases:
            raise ConflictError("Dataset exceeds case budget")
        if (
            request.budget.cost_reservation_usd * len(dataset.cases) * 2
            > request.budget.max_cost_usd
        ):
            raise ConflictError("Scheduling budget must reserve one complete paired trial")
        if draft.spec.limits.timeout_seconds is None:
            raise ConflictError("Set a per-run timeoutSeconds before starting evolution")
        allowed = {"systemPrompt", *(f"skill:{s.name}" for s in draft.spec.skills)}
        if not set(request.allowed_targets) <= allowed:
            raise ConflictError("Only existing Prompt/Skill instructions may be patched")
        # One active job per source is not assumed; each job has independent immutable hashes.
        now = datetime.now(UTC)
        evidence: tuple[Evidence, ...] = ()
        if self.quality is not None:
            scores = await self.quality.list_scores(tenant, owner, draft.spec.name)
            evidence = tuple(
                Evidence(
                    sourceId=s.score_id,
                    kind="quality",
                    code=s.name,
                    detail="unknown" if s.value is None else f"observed value: {s.value}",
                )
                for s in scores
                if s.agent_version == draft.spec.version and (s.value is None or s.value < 1)
            )[:100]
        job = EvolutionJob(
            evidence=evidence,
            tenantId=tenant,
            ownerId=owner,
            jobId=job_id,
            requestHash=request_hash,
            agentName=draft.spec.name,
            sourceDraftId=draft.draft_id,
            objective=request.objective,
            baseline=draft.spec,
            baselineManifestHash=compiled.report.snapshot.content_hash,
            baselinePackageHash=compiled.report.package_hash,
            baselinePreviewVersion=f"evo-baseline-{job_id}",
            policyHash=await self._policy(tenant, owner),
            dataset=request.dataset,
            datasetHash=digest(_data(dataset)),
            allowedTargets=request.allowed_targets,
            budget=request.budget,
            createdAt=now,
            expiresAt=now + timedelta(seconds=request.budget.max_duration_seconds),
            history=(HistoryEntry(action="created", actor=owner, at=now),),
        )
        try:
            await self.repository.add(job)
        except ConflictError:
            old = await self.get(tenant, owner, job_id)
            if old.request_hash != request_hash:
                raise
            return old
        return job

    async def _frozen(self, job: EvolutionJob) -> None:
        dataset = await self.evals.get_dataset(
            job.tenant_id, job.owner_id, job.dataset.dataset_id, job.dataset.version
        )
        draft = await self.studio.get(job.tenant_id, job.owner_id, job.source_draft_id)
        if digest(_data(dataset)) != job.dataset_hash or _data(draft.spec) != _data(job.baseline):
            raise ConflictError("Frozen dataset or source draft changed; start a new job")
        if await self._policy(job.tenant_id, job.owner_id) != job.policy_hash:
            raise ConflictError("Capability policy changed; start a new job")

    @staticmethod
    def _active(job: EvolutionJob, revision: int) -> None:
        if job.revision != revision:
            raise ConflictError("Evolution revision changed; refresh before retrying")
        if job.status != "active" or datetime.now(UTC) >= job.expires_at:
            raise ConflictError("Evolution job is stopped or expired")

    @staticmethod
    def _candidate(job: EvolutionJob, candidate_id: str) -> Candidate:
        for candidate in job.candidates:
            if candidate.candidate_id == candidate_id:
                return candidate
        raise NotFoundError("Evolution candidate not found")

    async def _save(
        self, job: EvolutionJob, action: str, candidate: Candidate | None = None, **changes: Any
    ) -> EvolutionJob:
        if candidate is not None:
            changes["candidates"] = tuple(
                candidate if c.candidate_id == candidate.candidate_id else c for c in job.candidates
            )
        # Validate the next state instead of `model_copy`: this is the only write
        # path, and `model_copy` skips every field validator, so a status that is
        # not in the JobStatus literal would reach the row and only surface much
        # later as a validation error while reading it back.
        updated = EvolutionJob.model_validate(
            {
                **job.model_dump(),
                **changes,
                "revision": job.revision + 1,
                "history": (
                    *job.history,
                    HistoryEntry(
                        action=action,
                        actor=job.owner_id,
                        at=datetime.now(UTC),
                        resource=candidate.candidate_id if candidate else "",
                    ),
                ),
            }
        )
        await self.repository.replace(job.revision, updated)
        return updated

    async def propose(
        self, tenant: str, owner: str, job_id: str, request: ProposeCandidate
    ) -> EvolutionJob:
        job = await self.get(tenant, owner, job_id)
        self._active(job, request.expected_revision)
        await self._frozen(job)
        if len(job.candidates) >= job.budget.max_candidates:
            raise ConflictError("Candidate budget exhausted")
        spec = _data(job.baseline)
        diffs: list[str] = []
        targets = [p.target for p in request.patches]
        if len(set(targets)) != len(targets):
            raise ConflictError("Use one atomic patch per target")
        for patch in request.patches:
            if patch.target not in job.allowed_targets:
                raise ConflictError("Patch target is outside the frozen allowlist")
            if patch.target == "systemPrompt":
                container, key = spec, "systemPrompt"
            else:
                name = patch.target.removeprefix("skill:")
                container = next(s for s in spec["skills"] if s["name"] == name)
                key = "instructions"
            before = container[key]
            if before.count(patch.old_text) != 1 or patch.old_text == patch.new_text:
                raise ConflictError("Patch needs a unique exact oldText and a meaningful change")
            after = before.replace(patch.old_text, patch.new_text, 1)
            if patch.target == "systemPrompt":

                def sections(text: str) -> dict[str, str]:
                    pieces = re.split(r"(?m)^(## .+)$", text)
                    return dict(zip(pieces[1::2], pieces[2::2], strict=True))

                old_sections, new_sections = sections(before), sections(after)
                protected = ("## Safety boundaries", "## Evidence and tool use")
                if old_sections.keys() != new_sections.keys() or any(
                    old_sections.get(h) != new_sections.get(h) for h in protected
                ):
                    raise ConflictError(
                        "Safety/evidence sections and Prompt headings are immutable"
                    )
            container[key] = after
            diffs.extend(
                difflib.unified_diff(
                    before.splitlines(True),
                    after.splitlines(True),
                    fromfile=f"baseline/{patch.target}",
                    tofile=f"candidate/{patch.target}",
                )
            )
        candidate_hash = digest(
            [
                job.job_id,
                job.baseline_manifest_hash,
                job.baseline_package_hash,
                [_data(p) for p in request.patches],
                job.policy_hash,
            ]
        )
        candidate_id = "candidate_" + candidate_hash[:24]
        if any(c.candidate_id == candidate_id for c in job.candidates):
            raise ConflictError("This candidate already exists")
        spec["version"] = job.baseline.version.split("+")[0] + "+evo." + candidate_hash[:16]
        frozen_spec = AgentDraftSpec.model_validate(spec)
        compiled = await self.studio.compile_frozen(tenant, owner, job.source_draft_id, frozen_spec)
        candidate = Candidate(
            candidateId=candidate_id,
            candidateHash=candidate_hash,
            manifestHash=compiled.report.snapshot.content_hash,
            packageHash=compiled.report.package_hash,
            patches=request.patches,
            rationale=request.rationale,
            diff="".join(diffs),
            spec=frozen_spec,
            previewVersion="evo-preview-" + candidate_hash[:32],
            createdAt=datetime.now(UTC),
        )
        return await self._save(job, "candidate.proposed", candidates=(*job.candidates, candidate))

    async def evaluate(
        self, tenant: str, owner: str, job_id: str, candidate_id: str, revision: int
    ) -> EvolutionJob:
        job = await self.get(tenant, owner, job_id)
        self._active(job, revision)
        await self._frozen(job)
        candidate = self._candidate(job, candidate_id)
        if candidate.status == "evaluating":
            return await self.refresh(tenant, owner, job_id, revision)
        if candidate.status not in {"proposed", "insufficient_evidence"}:
            raise ConflictError("Candidate cannot start another trial in this state")
        if any(c.comparison and c.comparison.unknown_cost_count for c in job.candidates):
            raise ConflictError(
                "Resolve missing cost telemetry before scheduling further experiments"
            )
        if any(c.status == "evaluating" for c in job.candidates):
            raise ConflictError("Complete the active paired experiment before scheduling another")
        actual_cost = sum(
            (c.comparison.baseline_cost or 0) + (c.comparison.candidate_cost or 0)
            for c in job.candidates
            if c.comparison
        )
        if actual_cost >= job.budget.max_cost_usd:
            raise ConflictError("Observed experiment cost exhausted the job budget")
        if len(candidate.trials) >= job.budget.max_trials:
            raise ConflictError("Trial budget exhausted")
        dataset = await self.evals.get_dataset(
            tenant, owner, job.dataset.dataset_id, job.dataset.version
        )
        reserved = (sum(len(c.trials) for c in job.candidates) + 1) * len(dataset.cases) * 2
        if reserved * job.budget.cost_reservation_usd > job.budget.max_cost_usd:
            raise ConflictError("Trial exceeds the reserved cost budget")
        trial = Trial(trialId=f"{job_id}:{candidate_id}:{len(candidate.trials) + 1}")
        candidate = candidate.model_copy(
            update={
                "status": "evaluating",
                "trials": (*candidate.trials, trial),
                "comparison": None,
                "review": None,
            }
        )
        # Persist intent before any queue submission; refresh resumes after API/process failure.
        job = await self._save(job, "trial.reserved", candidate)
        return await self.refresh(tenant, owner, job_id, job.revision)

    async def _ensure_runs(self, job: EvolutionJob, candidate: Candidate, trial: Trial) -> Trial:
        if trial.baseline_run_id is not None and trial.candidate_run_id is not None:
            return trial
        draft = await self.studio.get(job.tenant_id, job.owner_id, job.source_draft_id)
        ids: list[str] = []
        for label, spec, preview, manifest, package in (
            (
                "baseline",
                job.baseline,
                job.baseline_preview_version,
                job.baseline_manifest_hash,
                job.baseline_package_hash,
            ),
            (
                "candidate",
                candidate.spec,
                candidate.preview_version,
                candidate.manifest_hash,
                candidate.package_hash,
            ),
        ):
            compiled = await self.studio.compile_frozen(
                job.tenant_id, job.owner_id, job.source_draft_id, spec
            )
            if (compiled.report.snapshot.content_hash, compiled.report.package_hash) != (
                manifest,
                package,
            ):
                raise ConflictError("Compiled artifacts changed since candidate freezing")
            await self.agents.register_preview_snapshot(
                job.tenant_id,
                job.owner_id,
                compiled.report.snapshot,
                version=preview,
                package_hash=package,
                agent_id=draft.agent_id,
            )
            result = await self.evals.create_run(
                tenant_id=job.tenant_id,
                user_id=job.owner_id,
                allow_preview=True,
                request=CreateEvalRunRequest(
                    datasetId=job.dataset.dataset_id,
                    datasetVersion=job.dataset.version,
                    agentName=job.agent_name,
                    agentVersion=preview,
                    idempotencyKey=f"{trial.trial_id}:{label}",
                ),
            )
            current = await self.get(job.tenant_id, job.owner_id, job.job_id)
            if current.status != "active" or datetime.now(UTC) >= current.expires_at:
                await self.evals.cancel_run(
                    tenant_id=job.tenant_id,
                    user_id=job.owner_id,
                    eval_run_id=result.run.eval_run_id,
                )
                raise ConflictError("Job stopped while dispatching")
            ids.append(result.run.eval_run_id)
        return trial.model_copy(update={"baseline_run_id": ids[0], "candidate_run_id": ids[1]})

    async def refresh(self, tenant: str, owner: str, job_id: str, revision: int) -> EvolutionJob:
        job = await self.get(tenant, owner, job_id)
        if revision != job.revision:
            raise ConflictError("Evolution revision changed")
        if job.status != "active":
            return job
        if datetime.now(UTC) >= job.expires_at:
            return await self.cancel(tenant, owner, job_id, revision, expired=True)
        await self._frozen(job)
        for candidate in job.candidates:
            if candidate.status != "evaluating":
                continue
            trials: list[Trial] = []
            views: list[tuple[EvalRunView, EvalRunView]] = []
            for trial in candidate.trials:
                trial = await self._ensure_runs(job, candidate, trial)
                trials.append(trial)
                views.append(
                    (
                        await self.evals.get_run(tenant, owner, trial.baseline_run_id or ""),
                        await self.evals.get_run(tenant, owner, trial.candidate_run_id or ""),
                    )
                )
            candidate = candidate.model_copy(update={"trials": tuple(trials)})
            if all(a.run.status.is_terminal and b.run.status.is_terminal for a, b in views):
                comparison, evidence = self.compare(candidate, views, job.budget.max_cost_usd)
                candidate = candidate.model_copy(
                    update={
                        "comparison": comparison,
                        "status": "review_pending"
                        if comparison.status == "passed"
                        else "insufficient_evidence",
                    }
                )
                job = await self._save(
                    job,
                    "trial.compared",
                    candidate,
                    evidence=tuple(
                        {(e.source_id, e.code): e for e in (*job.evidence, *evidence)}.values()
                    ),
                )
            else:
                if candidate != self._candidate(job, candidate.candidate_id):
                    job = await self._save(job, "trial.dispatched", candidate)
        return job

    @staticmethod
    def compare(
        candidate: Candidate, views: list[tuple[EvalRunView, EvalRunView]], budget: float
    ) -> tuple[Comparison, tuple[Evidence, ...]]:
        improved: list[str] = []
        regressed: list[str] = []
        unresolved: list[str] = []
        costs: list[list[float]] = [[], []]
        unknown, count = 0, 0
        evidence: list[Evidence] = []
        for trial, (base, new) in enumerate(views, 1):
            left, right = {c.case_id: c for c in base.cases}, {c.case_id: c for c in new.cases}
            if len(left) != base.total_cases or left.keys() != right.keys():
                unresolved.append(f"trial-{trial}:incomplete_cases")
            for case_id in sorted(left.keys() | right.keys()):
                a, b = left.get(case_id), right.get(case_id)
                key = f"trial-{trial}:{case_id}"
                if a is None or b is None:
                    unresolved.append(key)
                    continue
                count += 1
                if a.status.value not in {"passed", "failed"} or b.status.value not in {
                    "passed",
                    "failed",
                }:
                    unresolved.append(key)
                elif not a.passed and b.passed:
                    improved.append(key)
                elif a.passed and not b.passed:
                    regressed.append(key)
                elif not b.passed:
                    unresolved.append(key)
                for side, result, view in ((0, a, base), (1, b, new)):
                    if result.usage.cost_usd is None:
                        unknown += 1
                    else:
                        costs[side].append(result.usage.cost_usd)
                    for failure in result.failure_details:
                        evidence.append(
                            Evidence(
                                sourceId=f"{view.run.eval_run_id}:{case_id}",
                                kind="eval",
                                code=failure.code,
                                detail=failure.detail[:1000],
                            )
                        )
            if base.run.status.value == "cancelled" or new.run.status.value != "passed":
                unresolved.append(f"trial-{trial}:candidate_not_passed")
        if sum(map(sum, costs)) > budget:
            unresolved.append("cost_budget_exceeded")
        state = (
            "regression"
            if regressed
            else (
                "passed" if improved and not unresolved and not unknown else "insufficient_evidence"
            )
        )
        payload = {
            "candidate": candidate.candidate_hash,
            "runs": [[_data(a), _data(b)] for a, b in views],
            "status": state,
            "unknownCost": unknown,
        }
        return Comparison(
            status=state,
            reportHash=digest(payload),
            caseCount=count,
            improved=tuple(improved),
            regressed=tuple(regressed),
            unresolved=tuple(unresolved),
            unknownCostCount=unknown,
            baselineCost=sum(costs[0]) if not unknown else None,
            candidateCost=sum(costs[1]) if not unknown else None,
            conclusion="固定验证集的工程对照；不代表独立留出集或真实业务收益。",
        ), tuple(evidence)

    async def review(
        self, tenant: str, owner: str, job_id: str, candidate_id: str, request: ReviewRequest
    ) -> EvolutionJob:
        job = await self.get(tenant, owner, job_id)
        self._active(job, request.expected_revision)
        await self._frozen(job)
        candidate = self._candidate(job, candidate_id)
        report = candidate.comparison
        if report is None or report.report_hash != request.report_hash:
            raise ConflictError("Review must bind the current server-generated report hash")
        if candidate.status not in {"review_pending", "insufficient_evidence"}:
            raise ConflictError("Candidate is not awaiting a decision")
        if request.decision == "approve" and report.status != "passed":
            raise ConflictError("Cannot approve regressions or insufficient evidence")
        now = datetime.now(UTC)
        review = Review(
            reviewer=owner,
            decision=request.decision,
            reason=request.reason,
            candidateHash=candidate.candidate_hash,
            reportHash=report.report_hash,
            policyHash=job.policy_hash,
            createdAt=now,
            expiresAt=min(job.expires_at, now + timedelta(hours=24)),
        )
        return await self._save(
            job,
            "candidate." + request.decision,
            candidate.model_copy(
                update={
                    "review": review,
                    "status": "approved" if request.decision == "approve" else "rejected",
                }
            ),
        )

    async def publication_guard(
        self, tenant: str, owner: str, snapshot: AgentManifestSnapshot, package_hash: str | None
    ) -> None:
        name, version = snapshot.manifest.metadata.name, snapshot.manifest.metadata.version
        for job in await self.list(tenant, owner):
            if job.agent_name != name:
                continue
            if (snapshot.content_hash, package_hash) == (
                job.baseline_manifest_hash,
                job.baseline_package_hash,
            ):
                continue
            matches = [c for c in job.candidates if c.spec.version == version]
            if not matches:
                if job.status == "active":
                    raise ConflictError(
                        "Active evolution owns publication; finish or cancel it first"
                    )
                continue
            candidate = matches[0]
            if candidate.status == "released" and (snapshot.content_hash, package_hash) == (
                candidate.manifest_hash,
                candidate.package_hash,
            ):
                continue
            review = candidate.review
            if (
                candidate.status != "releasing"
                or review is None
                or review.decision != "approve"
                or review.expires_at <= datetime.now(UTC)
                or job.status != "active"
                or candidate.comparison is None
                or review.report_hash != candidate.comparison.report_hash
                or review.candidate_hash != candidate.candidate_hash
                or review.policy_hash != job.policy_hash
                or (snapshot.content_hash, package_hash)
                != (candidate.manifest_hash, candidate.package_hash)
            ):
                raise ConflictError(
                    "Evolution version requires a current, hash-bound release approval"
                )
            await self._frozen(job)

    async def release(
        self, tenant: str, owner: str, job_id: str, candidate_id: str, revision: int
    ) -> EvolutionJob:
        job = await self.get(tenant, owner, job_id)
        candidate = self._candidate(job, candidate_id)
        if candidate.status == "released":
            return job
        self._active(job, revision)
        await self._frozen(job)
        if candidate.status not in {"approved", "releasing"}:
            raise ConflictError("Only an approved candidate can be released")
        if candidate.review is None or candidate.review.expires_at <= datetime.now(UTC):
            raise ConflictError("Release approval expired")
        compiled = await self.studio.compile_frozen(
            tenant, owner, job.source_draft_id, candidate.spec
        )
        if (compiled.report.snapshot.content_hash, compiled.report.package_hash) != (
            candidate.manifest_hash,
            candidate.package_hash,
        ):
            raise ConflictError("Candidate artifacts changed before publication")
        if candidate.status == "approved":
            candidate = candidate.model_copy(update={"status": "releasing"})
            job = await self._save(job, "release.reserved", candidate)
        version = await self.agents.publish_bundle(tenant, owner, compiled.bundle)
        candidate = candidate.model_copy(
            update={"status": "released", "released_version": version.version}
        )
        return await self._save(job, "candidate.released", candidate)

    async def rollback(
        self, tenant: str, owner: str, job_id: str, candidate_id: str, revision: int
    ) -> EvolutionJob:
        job = await self.get(tenant, owner, job_id)
        self._active(job, revision)
        candidate = self._candidate(job, candidate_id)
        if candidate.status != "released":
            raise ConflictError("Only a released candidate can be rolled back")
        compiled = await self.studio.compile_frozen(
            tenant, owner, job.source_draft_id, job.baseline
        )
        if (compiled.report.snapshot.content_hash, compiled.report.package_hash) != (
            job.baseline_manifest_hash,
            job.baseline_package_hash,
        ):
            raise ConflictError("Baseline artifacts changed; use the deployment snapshot rollback")
        await self.agents.publish_bundle(tenant, owner, compiled.bundle)
        return await self._save(
            job,
            "personal_version.rolled_back",
            candidate.model_copy(update={"status": "rolled_back"}),
            status="completed",
        )

    async def cancel(
        self, tenant: str, owner: str, job_id: str, revision: int, *, expired: bool = False
    ) -> EvolutionJob:
        job = await self.get(tenant, owner, job_id)
        if job.revision != revision:
            raise ConflictError("Evolution revision changed")
        if any(c.status == "releasing" for c in job.candidates):
            raise ConflictError("Resume the pending publication before cancelling")
        # Stop new dispatch before cancelling durable child runs. Retrying completes cancellation.
        if job.status == "active":
            job = await self._save(
                job,
                "job.expired" if expired else "job.cancelled",
                status="budget_exhausted" if expired else "cancelled",
                candidates=tuple(
                    c
                    if c.status in {"released", "rolled_back", "rejected"}
                    else c.model_copy(update={"status": "cancelled"})
                    for c in job.candidates
                ),
            )
        for candidate in job.candidates:
            for trial in candidate.trials:
                # Recover orphaned queue writes after a crash between child submission and CAS.
                for run in await self.evals.list_runs(tenant, owner):
                    if run.run.idempotency_key.startswith(trial.trial_id + ":"):
                        await self.evals.cancel_run(
                            tenant_id=tenant, user_id=owner, eval_run_id=run.run.eval_run_id
                        )
        return job

    async def add_experience(
        self, tenant: str, owner: str, job_id: str, request: ExperienceRequest
    ) -> EvolutionJob:
        job = await self.get(tenant, owner, job_id)
        if job.revision != request.expected_revision:
            raise ConflictError("Evolution revision changed")
        candidate = self._candidate(job, request.candidate_id)
        if candidate.comparison is None:
            raise ConflictError("Experience requires evaluated evidence")
        if len(job.experiences) >= 50:
            raise ConflictError("Experience limit reached")
        exp = Experience(
            experienceId="exp_" + digest([job_id, request.content, request.conditions])[:24],
            kind=request.kind,
            content=request.content,
            conditions=request.conditions,
            sourceCandidateId=candidate.candidate_id,
            expiresAt=datetime.now(UTC) + timedelta(days=90),
        )
        if any(e.experience_id == exp.experience_id for e in job.experiences):
            raise ConflictError("Experience already exists")
        return await self._save(job, "experience.observed", experiences=(*job.experiences, exp))

    async def set_experience(
        self, tenant: str, owner: str, job_id: str, experience_id: str, revision: int, status: str
    ) -> EvolutionJob:
        job = await self.get(tenant, owner, job_id)
        self._active(job, revision)
        exp = next((e for e in job.experiences if e.experience_id == experience_id), None)
        if exp is None:
            raise NotFoundError("Experience not found")
        if status not in {"reviewed", "deprecated"} or exp.status == "deprecated":
            raise ConflictError("Invalid experience transition")
        updated = exp.model_copy(update={"status": status, "version": exp.version + 1})
        return await self._save(
            job,
            "experience." + status,
            experiences=tuple(updated if e == exp else e for e in job.experiences),
        )

    async def observe(
        self, tenant: str, owner: str, job_id: str, candidate_id: str, revision: int
    ) -> EvolutionJob:
        job = await self.get(tenant, owner, job_id)
        self._active(job, revision)
        candidate = self._candidate(job, candidate_id)
        if candidate.released_version is None or self.quality is None:
            raise ConflictError("Observation requires a released candidate and quality collection")
        scores = [
            s
            for s in await self.quality.list_scores(tenant, owner, job.agent_name)
            if s.agent_version == candidate.released_version
        ]
        terminal = {s.run_id: s for s in scores if s.name == "terminal_success"}
        costs = {s.run_id: s for s in scores if s.name == "cost_budget"}
        feedback = [s.value for s in scores if s.name == "user_feedback" and s.value is not None]
        observation = Observation(
            candidateId=candidate_id,
            agentVersion=candidate.released_version,
            totalRuns=len(terminal),
            succeededRuns=sum(s.value == 1 for s in terminal.values()),
            unknownCostRuns=sum(r not in costs or costs[r].value is None for r in terminal),
            feedbackCount=len(feedback),
            feedbackMean=sum(feedback) / len(feedback) if feedback else None,
            observedAt=datetime.now(UTC),
            conclusion="暂无发布后样本，不能判断业务收益。"
            if not terminal
            else "仅汇总当前所有者的该发布版本样本；不构成业务收益因果结论。",
        )
        return await self._save(
            job, "release.observed", candidate, observations=(*job.observations[-31:], observation)
        )
