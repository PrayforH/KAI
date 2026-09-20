from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from harness.api.dependencies import ApiContainer, build_memory_container
from harness.core.errors import ConflictError
from harness.deployments.models import EnvironmentName, PromoteRequest
from harness.evals.models import CreateEvalDatasetVersionRequest
from harness.quality.controller import QualitySyncController
from harness.quality.langfuse import FakeQualityExporter, LangfuseQualityExporter
from harness.quality.models import (
    AlertRule,
    AlertState,
    CreateAlertRuleRequest,
    HumanFeedbackRequest,
    QualitySyncStatus,
)
from harness.studio.models import AgentTemplate, CreateAgentDraftRequest


async def seed_run(container: ApiContainer, name: str = "quality-agent"):
    draft = await container.studio.create(
        tenant_id="tenant-a",
        user_id="builder-a",
        request=CreateAgentDraftRequest(
            name=name,
            domain="quality",
            displayName="质量 Agent",
            description="验证规则 Score、人工反馈和告警门禁。",
            template=AgentTemplate.ANALYST,
        ),
    )
    version = await container.studio.publish(
        tenant_id="tenant-a", user_id="builder-a", draft_id=draft.draft_id
    )
    session = await container.sessions.create("tenant-a", "builder-a", name, version.version)
    await container.runs.create(
        "tenant-a", session.session_id, "quality-run", input={"prompt": "hello"}
    )
    task = await container.task_queue.dequeue()
    assert task is not None
    result = await container.worker.execute(task.tenant_id, task.run_id)
    await container.task_queue.acknowledge(task)
    return draft, version, session, result


@pytest.mark.asyncio
async def test_rule_scores_human_feedback_alert_and_promotion_gate() -> None:
    container = build_memory_container()
    draft, version, session, run = await seed_run(container)
    scores = await container.quality.record_terminal_run(run, session, "a" * 32)
    assert {item.name for item in scores} == {
        "terminal_success",
        "tool_reliability",
        "approval_completion",
        "duration_budget",
        "cost_budget",
        "artifact_integrity",
    }
    assert all(item.agent_version == version.version for item in scores)
    rule = AlertRule(
        tenantId="tenant-a",
        ruleId="feedback-floor",
        agentName=draft.spec.name,
        scoreName="user_feedback",
        minimumValue=0.8,
        minimumSamples=1,
        blocksPromotion=True,
        dashboardUrl="https://langfuse.example/project/scores",
        createdBy="builder-a",
        createdAt=datetime.now(UTC),
    )
    await container.quality.add_rule(rule)
    feedback = await container.quality.human_feedback(
        tenant_id="tenant-a",
        user_id="builder-a",
        run_id=run.run_id,
        request=HumanFeedbackRequest(value=0),
    )
    incidents = await container.quality.list_incidents("tenant-a", "builder-a", draft.spec.name)
    gate = await container.quality.gate("tenant-a", "builder-a", draft.spec.name, version.version)
    assert feedback.source.value == "human"
    assert incidents[0].state is AlertState.OPEN
    assert gate.passed is False

    with pytest.raises(ConflictError, match="blocking quality"):
        await container.deployments.promote(
            tenant_id="tenant-a",
            user_id="builder-a",
            request=PromoteRequest(
                agentName=draft.spec.name,
                agentVersion=version.version,
                environment=EnvironmentName.PRODUCTION,
                expectedEnvironmentRevision=0,
                imageDigest="sha256:" + "b" * 64,
                executionProfile="isolated-default",
                idempotencyKey="blocked-release",
            ),
        )


@pytest.mark.asyncio
async def test_export_failure_retries_without_changing_run_terminal_state() -> None:
    container = build_memory_container()
    _draft, _version, session, run = await seed_run(container, "retry-quality-agent")
    await container.quality.record_terminal_run(run, session, "c" * 32)
    exporter = FakeQualityExporter(fail=True)
    controller = QualitySyncController(
        repository=container.quality_repository,
        queue=container.quality_controller._queue,  # pyright: ignore[reportPrivateUsage]
        exporter=exporter,
    )
    job = await controller.process_once()
    assert job is not None
    assert job.status is QualitySyncStatus.RETRYING  # type: ignore[attr-defined]
    assert job.error_code == "quality_export_unavailable"  # type: ignore[attr-defined]
    assert (await container.runs.get("tenant-a", run.run_id)).status == run.status


@pytest.mark.asyncio
async def test_terminal_run_without_trace_is_counted_as_incomplete() -> None:
    container = build_memory_container()
    await seed_run(container, "missing-trace-agent")

    assert (
        container.reliability_metrics.count(
            "harness_trace_terminal_total", labels={"completeness": "missing"}
        )
        == 1
    )


@pytest.mark.asyncio
async def test_dataset_projection_and_langfuse_payload_are_metadata_only() -> None:
    container = build_memory_container()
    draft, _version, session, run = await seed_run(container, "dataset-quality-agent")
    dataset = await container.evals.create_dataset_version(
        tenant_id="tenant-a",
        user_id="builder-a",
        request=CreateEvalDatasetVersionRequest(
            draftId=draft.draft_id,
            expectedRevision=(
                await container.studio.get("tenant-a", "builder-a", draft.draft_id)
            ).revision,
            name="发布必测集",
        ),
    )
    projection = await container.quality.project_dataset(dataset)
    score = (await container.quality.record_terminal_run(run, session, "d" * 32))[0]
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json={"id": "ok"})

    exporter = LangfuseQualityExporter(
        base_url="https://langfuse.example",
        public_key="pk-test",
        secret_key=SecretStr("sk-test"),
        transport=httpx.MockTransport(handler),
    )
    await exporter.export_score(score)
    await exporter.export_dataset(projection)
    score_payload = json.loads(captured[0].content)
    dataset_payload = json.loads(captured[1].content)
    assert set(score_payload) == {"id", "traceId", "sessionId", "name", "value", "dataType"}
    assert set(dataset_payload["metadata"]) == {"agentName", "caseCount", "contentHash"}
    assert "prompt" not in repr(score_payload).lower()
    assert "prompt" not in repr(dataset_payload).lower()


def test_llm_judge_cannot_be_the_only_automatic_blocker() -> None:
    with pytest.raises(ValidationError, match="cannot directly block"):
        CreateAlertRuleRequest(
            agentName="quality-agent",
            scoreName="llm_judge.correctness",
            minimumValue=0.8,
            blocksPromotion=True,
        )


@pytest.mark.asyncio
async def test_quality_without_telemetry_is_durable_and_unknown_cost_is_not_zero() -> None:
    container = build_memory_container()
    draft, _, session, run = await seed_run(container, "quality-without-trace")
    first = await container.quality.record_terminal_run(run, session, "")
    second = await container.quality.record_terminal_run(run, session, "")
    assert first == second
    assert len(first) == 6 and all(score.trace_id is None for score in first)
    assert next(score for score in first if score.name == "cost_budget").value is None
    feedback = await container.quality.human_feedback(
        tenant_id="tenant-a", user_id="builder-a", run_id=run.run_id,
        request=HumanFeedbackRequest(value=0),
    )
    assert feedback.value == 0 and feedback.trace_id is None
    attached = await container.quality.record_terminal_run(run, session, "f" * 32)
    assert all(score.trace_id == "f" * 32 for score in attached)
    scores = await container.quality.list_scores("tenant-a", "builder-a", draft.spec.name)
    assert len(scores) == 7
