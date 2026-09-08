import os
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from harness.evals.models import (
    EvalCaseResult,
    EvalCaseStatus,
    EvalDatasetVersion,
    EvalRun,
    EvalRunStatus,
)
from harness.evals.suite import EvalCase
from harness.storage.database import SessionFactory, create_database
from harness.storage.eval_repository import (
    PostgresEvalDatasetRepository,
    PostgresEvalRunRepository,
)

DatabaseFixture = tuple[AsyncEngine, SessionFactory]
DATABASE_URL = os.getenv(
    "HARNESS_TEST_DATABASE_URL",
    "postgresql+asyncpg://harness:harness@127.0.0.1:5432/harness_test",
)
NOW = datetime(2026, 7, 16, tzinfo=UTC)


def dataset() -> EvalDatasetVersion:
    return EvalDatasetVersion(
        tenantId="tenant-a",
        datasetId="dataset-release",
        version=1,
        name="发布必测集",
        agentName="evaluated-agent",
        required=True,
        sourceDraftId="draft-one",
        sourceDraftRevision=3,
        sourceContentHash="a" * 64,
        sourcePackageHash="b" * 64,
        cases=(EvalCase(id="happy", tags=("happy",), prompt="hello"),),
        createdBy="builder-a",
        createdAt=NOW,
    )


def eval_run() -> EvalRun:
    return EvalRun(
        tenantId="tenant-a",
        evalRunId="eval-run-one",
        datasetId="dataset-release",
        datasetVersion=1,
        agentName="evaluated-agent",
        agentVersion="1.0.0",
        requestedBy="evaluator-a",
        idempotencyKey="release-1",
        status=EvalRunStatus.RUNNING,
        createdAt=NOW,
        updatedAt=NOW,
    )


@pytest.mark.asyncio
async def test_eval_dataset_run_and_case_survive_engine_restart(
    database: DatabaseFixture,
) -> None:
    first_engine, sessions = database
    datasets = PostgresEvalDatasetRepository(sessions)
    runs = PostgresEvalRunRepository(sessions)
    source = dataset()
    active = eval_run()
    result = EvalCaseResult(
        tenantId="tenant-a",
        evalRunId=active.eval_run_id,
        caseId="happy",
        sessionId="eval-session-one",
        runId="run-one",
        status=EvalCaseStatus.PASSED,
        passed=True,
        durationSeconds=1.25,
        completedAt=NOW,
    )
    await datasets.add(source)
    await runs.add(active)
    await runs.add_case_result(result)

    advanced = active.model_copy(update={"next_case_index": 1, "fencing_token": 1})
    assert await runs.compare_and_set(EvalRunStatus.RUNNING, advanced) is True
    stale = active.model_copy(update={"fencing_token": 1})
    assert await runs.compare_and_set(EvalRunStatus.RUNNING, stale) is False

    await first_engine.dispose()
    second_engine, second_sessions = create_database(DATABASE_URL)
    try:
        restored_dataset = await PostgresEvalDatasetRepository(second_sessions).get(
            "tenant-a", source.created_by, source.dataset_id, source.version
        )
        restored_run = await PostgresEvalRunRepository(second_sessions).get(
            "tenant-a", active.eval_run_id
        )
        restored_results = await PostgresEvalRunRepository(second_sessions).list_case_results(
            "tenant-a", active.eval_run_id
        )
    finally:
        await second_engine.dispose()

    assert restored_dataset == source
    assert restored_run == advanced
    assert restored_results == [result]


@pytest.mark.asyncio
async def test_eval_dataset_versions_are_immutable_and_monotonic(
    database: DatabaseFixture,
) -> None:
    _engine, sessions = database
    repository = PostgresEvalDatasetRepository(sessions)
    first = dataset()
    await repository.add(first)

    assert await repository.next_version("tenant-a", first.created_by, first.dataset_id) == 2
    second = first.model_copy(update={"version": 2, "source_draft_revision": 4})
    await repository.add(second)

    assert [item.version for item in await repository.list_for_tenant("tenant-a")] == [
        2,
        1,
    ]
