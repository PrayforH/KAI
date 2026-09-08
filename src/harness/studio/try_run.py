"""Studio Try Run contracts and observable Codex Loop projection."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field

from harness.agui.response import final_response_text
from harness.core.events import RunEvent
from harness.core.models import ApprovalRequest, Artifact, Run, RunStatus
from harness.evals.models import EvalDatasetVersion
from harness.studio.models import AgentDraft, PublishedAgentVersion, StudioModel


class CreateStudioTryRunRequest(StudioModel):
    expected_revision: int = Field(alias="expectedRevision", ge=1)
    prompt: str = Field(min_length=1, max_length=100_000)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=1, max_length=200)
    continue_from_run_id: str | None = Field(default=None, alias="continueFromRunId", min_length=1)
    input_artifact_ids: tuple[str, ...] = Field(default=(), alias="inputArtifactIds", max_length=20)


CodexLoopStageId = Literal["plan", "tools", "correction", "verification", "result"]
CodexLoopStageStatus = Literal["pending", "active", "completed", "skipped", "failed"]


class CodexLoopEvidence(StudioModel):
    event_type: str = Field(alias="eventType")
    sequence: int = Field(ge=0)
    summary: str


class CodexLoopStage(StudioModel):
    id: CodexLoopStageId
    label: str
    status: CodexLoopStageStatus
    summary: str
    evidence: tuple[CodexLoopEvidence, ...] = ()


class SolidifyStudioTryRunRequest(StudioModel):
    expected_revision: int = Field(alias="expectedRevision", ge=1)
    draft_revision: int = Field(alias="draftRevision", ge=1)
    run_id: str = Field(alias="runId", min_length=1)


class SolidifiedAgentResult(StudioModel):
    draft: AgentDraft
    version: PublishedAgentVersion
    dataset: EvalDatasetVersion
    loop: tuple[CodexLoopStage, ...] = Field(min_length=5, max_length=5)


class StudioTryRunView(StudioModel):
    draft_id: str = Field(alias="draftId")
    draft_revision: int = Field(alias="draftRevision", ge=1)
    run: Run
    events: tuple[RunEvent, ...] = ()
    approvals: tuple[ApprovalRequest, ...] = ()
    artifacts: tuple[Artifact, ...] = ()
    final_text: str = Field(default="", alias="finalText")
    activity: dict[str, Any] | None = None
    loop: tuple[CodexLoopStage, ...] = Field(min_length=5, max_length=5)


def final_text(events: list[RunEvent]) -> str:
    return final_response_text(events)


def _event_summary(event: RunEvent) -> str:
    payload = event.payload
    if event.type.startswith("tool."):
        name = payload.get("name") or payload.get("toolName") or "工具"
        if event.type == "tool.request":
            return f"请求调用 {name}"
        if event.type == "tool.result":
            failed = _tool_failed(event)
            return f"{name} 返回{'失败' if failed else '成功'}"
        if event.type == "tool.denied":
            return f"{name} 被策略拒绝"
        if event.type == "tool.allowed":
            return f"{name} 通过策略检查"
    if event.type == "artifact.ready":
        name = payload.get("name") or payload.get("filename") or "交付物"
        return f"交付物已就绪：{name}"
    if event.type == "message.completed":
        return "最终消息已完成"
    if event.type == "runtime.turn.completed":
        return "运行时回合已完成"
    if event.type == "runtime.error":
        return "运行时报告错误，进入修正判断"
    if event.type.startswith("approval."):
        return "高风险工具进入审批边界"
    labels = {
        "run.queued": "试跑已进入隔离执行队列",
        "run.provisioning": "正在准备隔离工作区",
        "run.running": "Agent 已开始执行",
        "run.succeeded": "试跑成功结束",
        "run.failed": "试跑失败结束",
        "run.rejected": "试跑被策略拒绝",
        "run.timed_out": "试跑超时结束",
        "run.cancelled": "试跑已取消",
    }
    return labels.get(event.type, event.type)


def _evidence(events: list[RunEvent], types: set[str]) -> tuple[CodexLoopEvidence, ...]:
    return tuple(
        CodexLoopEvidence(
            eventType=event.type,
            sequence=event.sequence,
            summary=_event_summary(event),
        )
        for event in events
        if event.type in types
    )[:8]


def _tool_failed(event: RunEvent) -> bool:
    if event.type in {"tool.denied", "runtime.error"}:
        return True
    if event.type != "tool.result":
        return False
    payload = event.payload
    exit_code = payload.get("exit_code")
    if isinstance(exit_code, int) and exit_code != 0:
        return True
    if payload.get("status") in {"failed", "error"}:
        return True
    if payload.get("success") is False or bool(payload.get("error")):
        return True
    output = payload.get("aggregated_output")
    return isinstance(output, str) and re.search(
        r"(?mi)^\s*exit(?:_code)?\s*=\s*[1-9]\d*\s*$", output
    ) is not None


def build_codex_loop(run: Run, events: list[RunEvent]) -> tuple[CodexLoopStage, ...]:
    """Project observable facts into five stages without exposing hidden reasoning."""

    terminal = run.status.is_terminal
    succeeded = run.status is RunStatus.SUCCEEDED
    started = bool(events) or run.status is not RunStatus.QUEUED
    prompt = str(run.input.get("prompt", "")).strip()
    prompt_summary = prompt[:96] + ("…" if len(prompt) > 96 else "")
    plan_types = {"run.queued", "run.provisioning", "run.running"}
    tool_types = {"tool.request", "tool.allowed", "tool.denied", "tool.result"}
    verify_types = {
        "tool.result",
        "artifact.ready",
        "message.completed",
        "runtime.turn.completed",
    }
    result_types = {
        "run.succeeded",
        "run.failed",
        "run.rejected",
        "run.timed_out",
        "run.cancelled",
    }
    tool_events = [event for event in events if event.type in tool_types]
    failures = [event for event in events if _tool_failed(event)]

    if tool_events:
        tools_status: CodexLoopStageStatus = "completed" if terminal else "active"
        tools_summary = f"记录 {len(tool_events)} 条真实工具决策与结果"
    elif terminal:
        tools_status = "skipped"
        tools_summary = "本次任务未触发工具调用"
    else:
        tools_status = "pending" if not started else "active"
        tools_summary = "等待 Agent 选择并调用已声明能力"

    correction_status: CodexLoopStageStatus = "failed" if failures else "skipped"
    correction_summary = (
        f"记录 {len(failures)} 次错误或拒绝；后续完成不代表问题已修复"
        if failures else "未观察到错误或拒绝"
    )

    if terminal:
        verification_status: CodexLoopStageStatus = "skipped"
        verification_summary = "未进行独立质量评测；执行完成不代表回答正确"
        result_status: CodexLoopStageStatus = "completed" if succeeded else "failed"
        result_summary = (
            "执行完成；请检查回答质量后再固化评测基线"
            if succeeded
            else f"试跑以 {run.status.value} 结束，不能固化"
        )
    else:
        verification_status = "pending"
        verification_summary = "未进行独立质量评测"
        result_status = "pending"
        result_summary = "等待运行结束"

    correction_types = {event.type for event in failures}
    return (
        CodexLoopStage(
            id="plan",
            label="运行准备",
            status="completed" if started else "active",
            summary=(
                f"锁定草稿修订与任务：{prompt_summary}"
                if prompt_summary
                else "锁定草稿修订、任务契约和隔离运行边界"
            ),
            evidence=_evidence(events, plan_types),
        ),
        CodexLoopStage(
            id="tools",
            label="工具调用",
            status=tools_status,
            summary=tools_summary,
            evidence=_evidence(events, tool_types),
        ),
        CodexLoopStage(
            id="correction",
            label="异常记录",
            status=correction_status,
            summary=correction_summary,
            evidence=_evidence(events, correction_types),
        ),
        CodexLoopStage(
            id="verification",
            label="验证",
            status=verification_status,
            summary=verification_summary,
            evidence=_evidence(events, verify_types),
        ),
        CodexLoopStage(
            id="result",
            label="结果",
            status=result_status,
            summary=result_summary,
            evidence=_evidence(events, result_types),
        ),
    )
