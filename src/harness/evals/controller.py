"""Recoverable, non-blocking Eval Run controller."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime

from harness.application.events import EventService
from harness.application.input_artifacts import InputArtifactService
from harness.application.runs import RunService
from harness.application.sessions import SessionService
from harness.core.errors import ConflictError
from harness.core.models import Run
from harness.core.ports import ArtifactStore, TaskQueue
from harness.evals.models import (
    EvalCaseResult,
    EvalCaseStatus,
    EvalDatasetVersion,
    EvalOutputArtifact,
    EvalRun,
    EvalRunStatus,
    transition_eval_run,
)
from harness.evals.queue import EvalTaskQueue
from harness.evals.repositories import EvalDatasetRepository, EvalRunRepository
from harness.evals.runner import (
    EvalCaseResult as ScoredCaseResult,
)
from harness.evals.runner import (
    EvalReport,
    RecordedRun,
    evaluate_recorded_run,
)
from harness.evals.suite import EvalCase
from harness.worker.main import RunExecutor


class EvalController:
    """Advance at most one durable state transition per leased task.

    The controller never waits for a child Run. It persists the child identity,
    requeues itself, and lets the normal Run worker execute before the next poll.
    """

    def __init__(
        self,
        *,
        datasets: EvalDatasetRepository,
        repository: EvalRunRepository,
        queue: EvalTaskQueue,
        sessions: SessionService,
        runs: RunService,
        events: EventService,
        inputs: InputArtifactService,
        object_store: ArtifactStore,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._datasets = datasets
        self._repository = repository
        self._queue = queue
        self._sessions = sessions
        self._runs = runs
        self._events = events
        self._inputs = inputs
        self._object_store = object_store
        self._clock = clock or (lambda: datetime.now(UTC))

    async def process_once(self) -> EvalRun | None:
        task = await self._queue.dequeue()
        if task is None:
            return None
        try:
            result = await self.reconcile(task.tenant_id, task.eval_run_id)
        except Exception:
            await self._queue.retry(task)
            raise
        if result.status.is_terminal:
            await self._queue.acknowledge(task)
        else:
            # Keep the same pending identity while atomically moving this lease
            # back to ready. ACK + enqueue would have a crash window that could
            # strand a non-terminal Eval Run with no durable task.
            await self._queue.retry(task)
        return result

    async def drain_locally(
        self,
        tenant_id: str,
        eval_run_id: str,
        *,
        run_queue: TaskQueue,
        executor: RunExecutor,
        max_steps: int = 200,
    ) -> EvalRun:
        """Drive both queues only for the explicit in-memory auto-execute mode."""

        for _step in range(max_steps):
            await self.process_once()
            task = await run_queue.dequeue()
            if task is not None:
                try:
                    await executor.execute(task.tenant_id, task.run_id)
                except Exception:
                    await run_queue.retry(task)
                    raise
                else:
                    await run_queue.acknowledge(task)
            current = await self._repository.get(tenant_id, eval_run_id)
            if current.status.is_terminal:
                return current
        raise TimeoutError("local Eval auto-execution did not converge")

    async def reconcile(self, tenant_id: str, eval_run_id: str) -> EvalRun:
        current = await self._repository.get(tenant_id, eval_run_id)
        if current.status.is_terminal:
            return current
        if current.status is EvalRunStatus.CANCELLING:
            return await self._cancel(current)
        if current.status is EvalRunStatus.QUEUED:
            return await self._replace(
                current,
                status=transition_eval_run(current.status, EvalRunStatus.RUNNING),
            )
        dataset = await self._datasets.get(
            tenant_id,
            current.requested_by,
            current.dataset_id,
            current.dataset_version,
        )
        if current.next_case_index >= len(dataset.cases):
            return await self._finalize(current)
        case = dataset.cases[current.next_case_index]
        try:
            if current.active_case_id is None:
                return await self._start_case(current, case.id)
            if current.active_case_id != case.id:
                raise RuntimeError("active Eval Case does not match durable index")
            if not current.active_input_artifact_ids and case.input_files:
                return await self._upload_inputs(current, dataset)
            if current.active_run_id is None:
                return await self._create_child_run(current, case.prompt, case.id)
            child = await self._runs.get(tenant_id, current.active_run_id)
            accepted_nonterminal = (
                child.status.value in case.expect.terminal_statuses and not child.status.is_terminal
            )
            if not child.status.is_terminal and not accepted_nonterminal:
                assert current.active_started_at is not None
                elapsed = (self._clock() - current.active_started_at).total_seconds()
                if elapsed > case.expect.max_duration_seconds:
                    await self._runs.cancel(tenant_id, child.run_id)
                    return await self._complete_case(
                        current,
                        EvalCaseResult(
                            tenantId=tenant_id,
                            evalRunId=current.eval_run_id,
                            caseId=case.id,
                            sessionId=current.active_session_id or "",
                            runId=child.run_id,
                            status=EvalCaseStatus.TIMED_OUT,
                            passed=False,
                            durationSeconds=max(0, elapsed),
                            failures=("case exceeded its configured timeout",),
                            completedAt=self._clock(),
                        ),
                    )
                return current
            return await self._score_case(current, case, child)
        except Exception as error:
            if current.active_run_id:
                try:
                    await self._runs.cancel(tenant_id, current.active_run_id)
                except Exception:
                    pass
            return await self._complete_case(
                current,
                EvalCaseResult(
                    tenantId=tenant_id,
                    evalRunId=current.eval_run_id,
                    caseId=case.id,
                    sessionId=current.active_session_id or "",
                    runId=current.active_run_id or "",
                    status=EvalCaseStatus.ERROR,
                    passed=False,
                    durationSeconds=self._active_duration(current),
                    failures=(f"evaluation infrastructure error ({type(error).__name__})",),
                    completedAt=self._clock(),
                ),
            )

    async def _start_case(self, current: EvalRun, case_id: str) -> EvalRun:
        digest = hashlib.sha256(
            f"{current.tenant_id}:{current.eval_run_id}:{case_id}".encode()
        ).hexdigest()[:24]
        session = await self._sessions.create(
            current.tenant_id,
            f"eval:{current.requested_by}",
            current.agent_name,
            current.agent_version,
            session_id=f"eval_session_{digest}",
            agent_owner_user_id=current.requested_by,
            **({"preview": True} if current.preview_execution else {}),
        )
        return await self._replace(
            current,
            active_case_id=case_id,
            active_session_id=session.session_id,
            active_started_at=self._clock(),
        )

    async def _upload_inputs(self, current: EvalRun, dataset: EvalDatasetVersion) -> EvalRun:
        case = dataset.cases[current.next_case_index]
        fixture_by_path = {item.path: item for item in dataset.fixtures}
        ids: list[str] = []
        for reference in case.input_files:
            fixture = fixture_by_path[reference.path]
            content = await self._object_store.get(current.tenant_id, fixture.object_id)
            uploaded = await self._inputs.upload(
                tenant_id=current.tenant_id,
                user_id=f"eval:{current.requested_by}",
                name=reference.path.rsplit("/", 1)[-1],
                media_type=reference.media_type,
                content=content,
            )
            ids.append(uploaded.input_artifact_id)
        return await self._replace(current, active_input_artifact_ids=tuple(ids))

    async def _create_child_run(self, current: EvalRun, prompt: str, case_id: str) -> EvalRun:
        assert current.active_session_id is not None
        digest = hashlib.sha256(
            f"{current.eval_run_id}:{current.dataset_version}:{case_id}".encode()
        ).hexdigest()[:32]
        child = await self._runs.create(
            current.tenant_id,
            current.active_session_id,
            f"eval-{digest}",
            input={
                "prompt": prompt,
                "input_artifact_ids": list(current.active_input_artifact_ids),
                "eval_run_id": current.eval_run_id,
                "eval_case_id": case_id,
            },
        )
        return await self._replace(current, active_run_id=child.run_id)

    async def _score_case(self, current: EvalRun, case: EvalCase, child: Run) -> EvalRun:
        events = await self._events.list_after(current.tenant_id, child.run_id, 0)
        duration = max(0, (child.updated_at - child.created_at).total_seconds())
        scored = evaluate_recorded_run(
            case,
            RecordedRun(
                run_id=child.run_id,
                status=child.status.value,
                duration_seconds=duration,
                events=tuple(event.model_dump(mode="json") for event in events),
            ),
        )
        if not child.status.is_terminal:
            await self._runs.cancel(current.tenant_id, child.run_id)
        return await self._complete_case(
            current,
            EvalCaseResult(
                tenantId=current.tenant_id,
                evalRunId=current.eval_run_id,
                caseId=scored.case_id,
                sessionId=current.active_session_id or "",
                runId=scored.run_id,
                status=(EvalCaseStatus.PASSED if scored.passed else EvalCaseStatus.FAILED),
                passed=scored.passed,
                durationSeconds=scored.duration_seconds,
                failures=scored.failures,
                failureDetails=scored.failure_details,
                usage=scored.usage,
                tools=scored.tools,
                approvalRequested=scored.approval_requested,
                subagents=scored.subagents,
                peakConcurrentSubagents=scored.peak_concurrent_subagents,
                completedAt=self._clock(),
            ),
        )

    async def _complete_case(self, current: EvalRun, result: EvalCaseResult) -> EvalRun:
        await self._repository.add_case_result(result)
        return await self._replace(
            current,
            next_case_index=current.next_case_index + 1,
            active_case_id=None,
            active_session_id=None,
            active_input_artifact_ids=(),
            active_run_id=None,
            active_started_at=None,
        )

    async def _cancel(self, current: EvalRun) -> EvalRun:
        if current.active_run_id:
            await self._runs.cancel(current.tenant_id, current.active_run_id)
        existing_case_ids = {
            item.case_id
            for item in await self._repository.list_case_results(
                current.tenant_id, current.eval_run_id
            )
        }
        if current.active_case_id and current.active_case_id not in existing_case_ids:
            await self._repository.add_case_result(
                EvalCaseResult(
                    tenantId=current.tenant_id,
                    evalRunId=current.eval_run_id,
                    caseId=current.active_case_id,
                    sessionId=current.active_session_id or "",
                    runId=current.active_run_id or "",
                    status=EvalCaseStatus.CANCELLED,
                    passed=False,
                    durationSeconds=self._active_duration(current),
                    failures=("evaluation cancelled",),
                    completedAt=self._clock(),
                )
            )
        current = await self._write_artifacts(current)
        return await self._replace(
            current,
            status=transition_eval_run(current.status, EvalRunStatus.CANCELLED),
            completed_at=self._clock(),
        )

    async def _finalize(self, current: EvalRun) -> EvalRun:
        results = await self._repository.list_case_results(current.tenant_id, current.eval_run_id)
        current = await self._write_artifacts(current)
        return await self._replace(
            current,
            status=transition_eval_run(
                current.status,
                EvalRunStatus.PASSED
                if results and all(item.passed for item in results)
                else EvalRunStatus.FAILED,
            ),
            completed_at=self._clock(),
        )

    async def _write_artifacts(self, current: EvalRun) -> EvalRun:
        if current.artifacts:
            return current
        records = await self._repository.list_case_results(current.tenant_id, current.eval_run_id)
        dataset = await self._datasets.get(
            current.tenant_id,
            current.requested_by,
            current.dataset_id,
            current.dataset_version,
        )
        by_case_id = {item.case_id: item for item in records}
        records = [by_case_id[item.id] for item in dataset.cases if item.id in by_case_id]
        report = EvalReport(
            agent=current.agent_name,
            agent_version=current.agent_version,
            cases=tuple(
                ScoredCaseResult(
                    case_id=item.case_id,
                    run_id=item.run_id,
                    status=item.status.value,
                    duration_seconds=item.duration_seconds,
                    passed=item.passed,
                    failures=item.failures,
                    tools=item.tools,
                    approval_requested=item.approval_requested,
                    subagents=item.subagents,
                    peak_concurrent_subagents=item.peak_concurrent_subagents,
                )
                for item in records
            ),
        )
        report_payload = report.to_dict()
        if current.status is EvalRunStatus.CANCELLING:
            report_payload["passed"] = False
        payloads = (
            (
                f"{current.eval_run_id}-report-json",
                "report.json",
                "application/json",
                json.dumps(report_payload, ensure_ascii=False, indent=2, sort_keys=True).encode(),
            ),
            (
                f"{current.eval_run_id}-junit-xml",
                "junit.xml",
                "application/xml",
                report.to_junit_xml().encode(),
            ),
        )
        artifacts: list[EvalOutputArtifact] = []
        for artifact_id, name, media_type, content in payloads:
            stored = await self._object_store.put(current.tenant_id, artifact_id, content)
            artifacts.append(
                EvalOutputArtifact(
                    artifactId=artifact_id,
                    name=name,
                    mediaType=media_type,
                    sha256=stored.sha256,
                    sizeBytes=stored.size_bytes,
                )
            )
        return await self._replace(current, artifacts=tuple(artifacts))

    async def _replace(self, current: EvalRun, **updates: object) -> EvalRun:
        updated = current.model_copy(
            update={
                **updates,
                "updated_at": self._clock(),
                "fencing_token": current.fencing_token + 1,
            }
        )
        if not await self._repository.compare_and_set(current.status, updated):
            raise ConflictError(f"Eval Run changed during reconcile: {current.eval_run_id}")
        return updated

    def _active_duration(self, current: EvalRun) -> float:
        if current.active_started_at is None:
            return 0
        return max(0, (self._clock() - current.active_started_at).total_seconds())
