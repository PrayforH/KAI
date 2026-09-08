"""Dataset versioning, Eval Run lifecycle and release-gate queries."""

from __future__ import annotations

import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from io import BytesIO
from typing import cast
from uuid import uuid4

from harness.auth.audit import AuditService
from harness.core.errors import ConflictError, NotFoundError
from harness.core.models import AgentVersionStatus
from harness.core.ports import AgentRegistry, ArtifactStore
from harness.evals.importer import (
    EvalImportError,
    cases_from_imported,
    parse_imported_cases,
)
from harness.evals.models import (
    CreateEvalDatasetVersionRequest,
    ImportEvalDatasetRequest,
    CreateEvalRunRequest,
    EvalDatasetVersion,
    EvalFixture,
    EvalGateResult,
    EvalRun,
    EvalRunStatus,
    EvalRunView,
    transition_eval_run,
)
from harness.evals.queue import EvalTask, EvalTaskQueue
from harness.evals.repositories import EvalDatasetRepository, EvalRunRepository
from harness.studio.preview_service import PreviewService
from harness.studio.service import AgentStudioService


def _default_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def _published_evaluation_enabled(snapshot: dict[str, object]) -> bool:
    manifest = snapshot.get("manifest")
    if not isinstance(manifest, dict):
        return True
    manifest = cast(dict[str, object], manifest)
    metadata = manifest.get("metadata")
    if not isinstance(metadata, dict):
        return True
    metadata = cast(dict[str, object], metadata)
    labels = metadata.get("labels")
    if not isinstance(labels, dict):
        return True
    labels = cast(dict[str, object], labels)
    return str(labels.get("evaluation-enabled", "true")).lower() != "false"


class EvalControlPlaneService:
    def __init__(
        self,
        *,
        datasets: EvalDatasetRepository,
        runs: EvalRunRepository,
        queue: EvalTaskQueue,
        studio: AgentStudioService,
        registry: AgentRegistry,
        object_store: ArtifactStore,
        previews: PreviewService | None = None,
        audit: AuditService | None = None,
        clock: Callable[[], datetime] | None = None,
        id_generator: Callable[[str], str] | None = None,
    ) -> None:
        self._datasets = datasets
        self._runs = runs
        self._queue = queue
        self._studio = studio
        self._registry = registry
        self._object_store = object_store
        self._previews = previews
        self._audit = audit
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_generator = id_generator or _default_id

    async def create_dataset_version(
        self,
        *,
        tenant_id: str,
        user_id: str,
        request: CreateEvalDatasetVersionRequest,
    ) -> EvalDatasetVersion:
        draft = await self._require_evaluable_draft(
            tenant_id, user_id, request.draft_id, request.expected_revision
        )
        return await self._create_version(
            tenant_id=tenant_id,
            user_id=user_id,
            draft=draft,
            name=request.name,
            required=request.required,
            dataset_id=request.dataset_id,
            cases=draft.spec.evaluation_cases,
            action="studio.eval_dataset.create",
        )

    async def import_dataset_version(
        self,
        *,
        tenant_id: str,
        user_id: str,
        request: ImportEvalDatasetRequest,
    ) -> EvalDatasetVersion:
        draft = await self._require_evaluable_draft(
            tenant_id, user_id, request.draft_id, request.expected_revision
        )
        try:
            imported = parse_imported_cases(request.format, request.content)
        except EvalImportError as error:
            raise ConflictError(f"question bank import rejected: {error}") from error
        return await self._create_version(
            tenant_id=tenant_id,
            user_id=user_id,
            draft=draft,
            name=request.name,
            required=request.required,
            dataset_id=request.dataset_id,
            cases=cases_from_imported(imported),
            action="studio.eval_dataset.import",
        )

    async def _require_evaluable_draft(
        self, tenant_id: str, user_id: str, draft_id: str, expected_revision: int
    ):
        draft = await self._studio.get(tenant_id, user_id, draft_id)
        if draft.revision != expected_revision:
            raise ConflictError(
                "Agent draft revision changed before Eval Dataset creation: "
                f"expected={expected_revision} actual={draft.revision}"
            )
        if not draft.spec.evaluation_enabled:
            raise ConflictError("Agent Eval is disabled for this draft")
        return draft

    async def _create_version(
        self,
        *,
        tenant_id: str,
        user_id: str,
        draft,
        name: str,
        required: bool,
        dataset_id: str | None,
        cases,
        action: str,
    ) -> EvalDatasetVersion:
        if not cases:
            raise ConflictError("evaluation dataset needs at least one case")
        compiled = await self._studio.bundle(tenant_id, user_id, draft.draft_id)
        resolved_dataset_id = dataset_id or self._id_generator("dataset")
        version = await self._datasets.next_version(tenant_id, user_id, resolved_dataset_id)
        fixtures: list[EvalFixture] = []
        with zipfile.ZipFile(BytesIO(compiled.bundle)) as archive:
            for path, media_type in sorted(
                {
                    (item.path, item.media_type)
                    for case in cases
                    for item in case.input_files
                }
            ):
                try:
                    content = archive.read(path)
                except KeyError as error:
                    raise ConflictError(
                        f"evaluation fixture is missing from the bundle: {path}"
                    ) from error
                object_id = self._id_generator("eval_fixture")
                stored = await self._object_store.put(tenant_id, object_id, content)
                fixtures.append(
                    EvalFixture(
                        path=path,
                        mediaType=media_type,
                        objectId=object_id,
                        sha256=stored.sha256,
                        sizeBytes=stored.size_bytes,
                    )
                )
        dataset = EvalDatasetVersion(
            tenantId=tenant_id,
            datasetId=resolved_dataset_id,
            version=version,
            name=name,
            agentName=draft.spec.name,
            required=required,
            sourceDraftId=draft.draft_id,
            sourceDraftRevision=draft.revision,
            sourceContentHash=compiled.report.snapshot.content_hash,
            sourcePackageHash=compiled.report.package_hash,
            cases=cases,
            fixtures=tuple(fixtures),
            createdBy=user_id,
            createdAt=self._clock(),
        )
        await self._datasets.add(dataset)
        await self._record(
            tenant_id=tenant_id,
            user_id=user_id,
            action=action,
            resource_type="eval_dataset_version",
            resource_id=f"{dataset.dataset_id}@{dataset.version}",
            details={
                "agent_name": dataset.agent_name,
                "required": dataset.required,
                "case_count": len(dataset.cases),
                "source_content_hash": dataset.source_content_hash,
                "source_package_hash": dataset.source_package_hash,
            },
        )
        return dataset

    async def list_datasets(self, tenant_id: str, owner_user_id: str) -> list[EvalDatasetVersion]:
        return [
            item
            for item in await self._datasets.list_for_tenant(tenant_id)
            if item.created_by == owner_user_id
        ]

    async def get_dataset(
        self, tenant_id: str, owner_user_id: str, dataset_id: str, version: int
    ) -> EvalDatasetVersion:
        return await self._datasets.get(tenant_id, owner_user_id, dataset_id, version)

    async def create_run(
        self,
        *,
        tenant_id: str,
        user_id: str,
        request: CreateEvalRunRequest,
    ) -> EvalRunView:
        existing = await self._runs.find_by_idempotency(tenant_id, user_id, request.idempotency_key)
        if existing is not None:
            self._ensure_same_run(existing, request)
            return await self._view(existing)
        dataset = await self._datasets.get(
            tenant_id, user_id, request.dataset_id, request.dataset_version
        )
        if dataset.agent_name != request.agent_name:
            raise ConflictError("Eval Dataset targets a different Agent")
        version = await self._registry.get(
            tenant_id, user_id, request.agent_name, request.agent_version
        )
        if version.status is not AgentVersionStatus.PUBLISHED:
            raise ConflictError("Eval Runs require a published Agent version")
        if not _published_evaluation_enabled(version.snapshot):
            raise ConflictError("Agent Eval is disabled for this version")
        if request.preview_id is not None:
            if self._previews is None:
                raise ConflictError("Preview association is not configured")
            preview = await self._previews.get(tenant_id, user_id, request.preview_id)
            if preview.stale or preview.status.value != "ready":
                raise ConflictError("Eval Run Preview must be ready and current")
            preview_draft = await self._studio.get(tenant_id, user_id, preview.draft_id)
            if preview_draft.spec.name != request.agent_name:
                raise ConflictError("Eval Run Preview targets a different Agent")
        now = self._clock()
        run = EvalRun(
            tenantId=tenant_id,
            evalRunId=self._id_generator("eval_run"),
            datasetId=dataset.dataset_id,
            datasetVersion=dataset.version,
            agentName=request.agent_name,
            agentVersion=request.agent_version,
            previewId=request.preview_id,
            environment=request.environment,
            requestedBy=user_id,
            idempotencyKey=request.idempotency_key,
            status=EvalRunStatus.QUEUED,
            createdAt=now,
            updatedAt=now,
        )
        try:
            await self._runs.add(run)
        except ConflictError:
            concurrent = await self._runs.find_by_idempotency(
                tenant_id, user_id, request.idempotency_key
            )
            if concurrent is None:
                raise
            self._ensure_same_run(concurrent, request)
            return await self._view(concurrent)
        await self._queue.enqueue(EvalTask(tenant_id=tenant_id, eval_run_id=run.eval_run_id))
        await self._record(
            tenant_id=tenant_id,
            user_id=user_id,
            action="studio.eval_run.create",
            resource_type="eval_run",
            resource_id=run.eval_run_id,
            details={
                "dataset_id": run.dataset_id,
                "dataset_version": run.dataset_version,
                "agent_name": run.agent_name,
                "agent_version": run.agent_version,
                "preview_id": run.preview_id or "",
                "environment": run.environment or "",
            },
        )
        return await self._view(run)

    async def get_run(self, tenant_id: str, owner_user_id: str, eval_run_id: str) -> EvalRunView:
        run = await self._runs.get(tenant_id, eval_run_id)
        if run.requested_by != owner_user_id:
            raise NotFoundError(f"Eval Run not found: {eval_run_id}")
        return await self._view(run)

    async def list_runs(self, tenant_id: str, owner_user_id: str) -> list[EvalRunView]:
        return [
            await self._view(run)
            for run in await self._runs.list_for_tenant(tenant_id)
            if run.requested_by == owner_user_id
        ]

    async def cancel_run(self, *, tenant_id: str, user_id: str, eval_run_id: str) -> EvalRunView:
        current = await self._runs.get(tenant_id, eval_run_id)
        if current.requested_by != user_id:
            raise NotFoundError(f"Eval Run not found: {eval_run_id}")
        if not current.status.is_terminal and current.status is not EvalRunStatus.CANCELLING:
            updated = current.model_copy(
                update={
                    "status": transition_eval_run(current.status, EvalRunStatus.CANCELLING),
                    "updated_at": self._clock(),
                    "fencing_token": current.fencing_token + 1,
                }
            )
            if not await self._runs.compare_and_set(current.status, updated):
                raise ConflictError("Eval Run changed during cancellation")
            current = updated
        if not current.status.is_terminal:
            await self._queue.enqueue(EvalTask(tenant_id=tenant_id, eval_run_id=eval_run_id))
        await self._record(
            tenant_id=tenant_id,
            user_id=user_id,
            action="studio.eval_run.cancel",
            resource_type="eval_run",
            resource_id=eval_run_id,
            details={"status": current.status.value},
        )
        return await self._view(current)

    async def gate(
        self,
        tenant_id: str,
        owner_user_id: str,
        agent_name: str,
        agent_version: str,
    ) -> EvalGateResult:
        version = await self._registry.get(tenant_id, owner_user_id, agent_name, agent_version)
        if not _published_evaluation_enabled(version.snapshot):
            return EvalGateResult(
                agentName=agent_name,
                agentVersion=agent_version,
                passed=True,
                requiredDatasets=0,
                passedDatasets=0,
                missingDatasetIds=(),
            )
        versions = [
            item
            for item in await self._datasets.list_for_tenant(tenant_id)
            if item.created_by == owner_user_id
        ]
        latest: dict[str, EvalDatasetVersion] = {}
        for dataset in versions:
            if dataset.agent_name != agent_name or not dataset.required:
                continue
            current = latest.get(dataset.dataset_id)
            if current is None or dataset.version > current.version:
                latest[dataset.dataset_id] = dataset
        passed = {
            (run.dataset_id, run.dataset_version)
            for run in await self._runs.list_for_tenant(tenant_id)
            if run.requested_by == owner_user_id
            if run.agent_name == agent_name
            and run.agent_version == agent_version
            and run.status is EvalRunStatus.PASSED
        }
        missing = tuple(
            sorted(
                dataset_id
                for dataset_id, dataset in latest.items()
                if (dataset_id, dataset.version) not in passed
            )
        )
        return EvalGateResult(
            agentName=agent_name,
            agentVersion=agent_version,
            passed=not missing,
            requiredDatasets=len(latest),
            passedDatasets=len(latest) - len(missing),
            missingDatasetIds=missing,
        )

    async def require_promotion_allowed(
        self,
        tenant_id: str,
        owner_user_id: str,
        agent_name: str,
        agent_version: str,
    ) -> EvalGateResult:
        gate = await self.gate(tenant_id, owner_user_id, agent_name, agent_version)
        if not gate.passed:
            raise ConflictError(
                "Agent version has not passed every required Eval Dataset: "
                + ", ".join(gate.missing_dataset_ids)
            )
        return gate

    async def download_artifact(
        self,
        tenant_id: str,
        owner_user_id: str,
        eval_run_id: str,
        artifact_id: str,
    ) -> tuple[str, str, bytes]:
        run = await self._runs.get(tenant_id, eval_run_id)
        if run.requested_by != owner_user_id:
            raise NotFoundError(f"Eval Run not found: {eval_run_id}")
        artifact = next((item for item in run.artifacts if item.artifact_id == artifact_id), None)
        if artifact is None:
            raise NotFoundError(f"Eval artifact not found: {artifact_id}")
        return (
            artifact.name,
            artifact.media_type,
            await self._object_store.get(tenant_id, artifact_id),
        )

    async def _view(self, run: EvalRun) -> EvalRunView:
        dataset = await self._datasets.get(
            run.tenant_id, run.requested_by, run.dataset_id, run.dataset_version
        )
        stored_cases = {
            item.case_id: item
            for item in await self._runs.list_case_results(run.tenant_id, run.eval_run_id)
        }
        cases = tuple(stored_cases[item.id] for item in dataset.cases if item.id in stored_cases)
        return EvalRunView(
            run=run,
            cases=cases,
            passedCases=sum(item.passed for item in cases),
            totalCases=len(dataset.cases),
        )

    @staticmethod
    def _ensure_same_run(existing: EvalRun, request: CreateEvalRunRequest) -> None:
        if (
            existing.dataset_id != request.dataset_id
            or existing.dataset_version != request.dataset_version
            or existing.agent_name != request.agent_name
            or existing.agent_version != request.agent_version
            or existing.preview_id != request.preview_id
            or existing.environment != request.environment
        ):
            raise ConflictError("Eval Run idempotency key was reused for another target")

    async def _record(
        self,
        *,
        tenant_id: str,
        user_id: str,
        action: str,
        resource_type: str,
        resource_id: str,
        details: dict[str, object],
    ) -> None:
        if self._audit is None:
            return
        await self._audit.record(
            tenant_id=tenant_id,
            user_id=user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            outcome="success",
            details=details,
        )
