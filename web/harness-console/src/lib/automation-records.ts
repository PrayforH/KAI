import type { Execution } from "./assistant-client";
import type { ApiAutomationRecordArtifact, ApiAutomationRunRecord, ApiSchedule } from "./studio-client";
import { triggerKind } from "../components/duty-run-history";

/** The 运行记录 tab lists two sources in one list: automation runs and duty
 *  (Objective) executions. Both are normalised to this shape so the tab keeps a
 *  single record card instead of two layouts. */
export type UnifiedRecordStatus = "running" | "success" | "failed" | "cancelled";

export type DutyRun = { duty: ApiSchedule; execution: Execution };

export type UnifiedRecord = {
  id: string;
  source: "automation" | "duty";
  sourceId: string;
  name: string;
  status: UnifiedRecordStatus;
  trigger: string;
  startedAt: string;
  finishedAt: string | null;
  durationMs: number | null;
  summary: string | null;
  error: string | null;
  artifacts: ApiAutomationRecordArtifact[];
  threadId: string | null;
  runId: string | null;
};

const DUTY_STATUS: Record<Execution["status"], UnifiedRecordStatus> = {
  pending: "running",
  running: "running",
  succeeded: "success",
  failed: "failed",
  cancelled: "cancelled",
};

const DUTY_TRIGGER: Record<ReturnType<typeof triggerKind>, string> = {
  schedule: "定时",
  manual: "手动",
  feishu: "飞书",
  delegate: "委派",
  manage: "管理",
  trial: "演练",
  other: "其他",
};

function fromAutomation(record: ApiAutomationRunRecord): UnifiedRecord {
  return {
    id: record.recordId,
    source: "automation",
    sourceId: record.taskId,
    name: record.taskName,
    status: record.status,
    trigger: record.trigger === "manual" ? "手动执行" : "计划触发",
    startedAt: record.startedAt,
    finishedAt: record.finishedAt ?? null,
    durationMs: record.durationMs ?? null,
    summary: record.outputSummary ?? null,
    error: record.error ?? null,
    artifacts: record.artifacts ?? [],
    threadId: record.threadId ?? null,
    runId: record.runId || null,
  };
}

function fromDuty({ duty, execution }: DutyRun): UnifiedRecord {
  return {
    id: execution.executionId,
    source: "duty",
    sourceId: duty.sourceId,
    name: duty.title,
    status: DUTY_STATUS[execution.status],
    trigger: DUTY_TRIGGER[triggerKind(execution.triggerKey)],
    startedAt: execution.createdAt,
    finishedAt: null,
    durationMs: null,
    summary: execution.summary || null,
    error: execution.status === "failed" ? execution.errorCode ?? null : null,
    artifacts: [],
    threadId: execution.threadId || null,
    runId: execution.runId || null,
  };
}

/** Newest first; equal timestamps keep their incoming order. */
export function mergeRunRecords(
  records: ApiAutomationRunRecord[],
  dutyRuns: DutyRun[],
): UnifiedRecord[] {
  return [
    ...records.map(fromAutomation),
    ...dutyRuns.map(fromDuty),
  ].sort((left, right) => {
    const leftTime = Date.parse(left.startedAt);
    const rightTime = Date.parse(right.startedAt);
    if (Number.isNaN(leftTime) || Number.isNaN(rightTime)) return 0;
    return rightTime - leftTime;
  });
}

export function filterRunRecords(
  records: UnifiedRecord[],
  { status, query }: { status: "all" | UnifiedRecordStatus; query: string },
): UnifiedRecord[] {
  const needle = query.trim().toLowerCase();
  return records.filter((record) => {
    if (status !== "all" && record.status !== status) return false;
    if (!needle) return true;
    return record.name.toLowerCase().includes(needle)
      || (record.summary ?? "").toLowerCase().includes(needle)
      || (record.error ?? "").toLowerCase().includes(needle);
  });
}
