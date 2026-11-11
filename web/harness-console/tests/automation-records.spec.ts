import { describe, expect, it } from "vitest";
import { filterRunRecords, mergeRunRecords, type DutyRun } from "../src/lib/automation-records";
import type { ApiAutomationRunRecord, ApiSchedule } from "../src/lib/studio-client";

function automationRecord(overrides: Partial<ApiAutomationRunRecord> = {}): ApiAutomationRunRecord {
  return {
    tenantId: "tenant",
    recordId: "record_1",
    taskId: "task_1",
    taskName: "每周工作周报",
    userId: "user",
    trigger: "scheduled",
    status: "success",
    threadId: "thread_1",
    sessionId: "session_1",
    runId: "run_1",
    startedAt: "2026-11-10T09:00:00Z",
    finishedAt: "2026-11-10T09:02:00Z",
    durationMs: 120_000,
    outputSummary: "本周合并 3 个 PR",
    ...overrides,
  };
}

const duty: ApiSchedule = {
  source: "objective",
  sourceId: "duty_1",
  title: "每日巡检",
  status: "active",
  revision: 4,
  nextRunAt: "2026-11-11T09:00:00Z",
  timing: { kind: "cron", expression: "0 9 * * *", timezone: "Asia/Shanghai" },
};

function dutyRun(overrides: Partial<DutyRun["execution"]> = {}, schedule = duty): DutyRun {
  return {
    duty: schedule,
    execution: {
      executionId: "execution_1",
      threadId: "thread_2",
      runId: "run_2",
      status: "succeeded",
      summary: "巡检通过",
      createdAt: "2026-11-11T09:00:00Z",
      triggerKey: "schedule:cron",
      ...overrides,
    },
  };
}

describe("mergeRunRecords", () => {
  it("lists automation runs and duty executions newest first", () => {
    const merged = mergeRunRecords(
      [automationRecord()],
      [dutyRun()],
    );
    expect(merged.map((record) => record.id)).toEqual(["execution_1", "record_1"]);
    expect(merged[0]).toMatchObject({
      source: "duty",
      sourceId: "duty_1",
      name: "每日巡检",
      status: "success",
      trigger: "定时",
      summary: "巡检通过",
      threadId: "thread_2",
      runId: "run_2",
    });
    expect(merged[1]).toMatchObject({
      source: "automation",
      sourceId: "task_1",
      status: "success",
      trigger: "计划触发",
      durationMs: 120_000,
      summary: "本周合并 3 个 PR",
    });
  });

  it("normalises duty execution states into the record vocabulary", () => {
    const statuses = (["pending", "running", "succeeded", "failed", "cancelled"] as const)
      .map((status, index) => mergeRunRecords([], [dutyRun({
        executionId: `execution_${index}`,
        status,
        errorCode: status === "failed" ? "model_error" : null,
      })])[0]);
    expect(statuses.map((record) => record.status))
      .toEqual(["running", "running", "success", "failed", "cancelled"]);
    expect(statuses[3].error).toBe("model_error");
  });

  it("labels manual automation runs and unknown duty triggers", () => {
    const [manual] = mergeRunRecords([automationRecord({ trigger: "manual" })], []);
    expect(manual.trigger).toBe("手动执行");
    const [other] = mergeRunRecords([], [dutyRun({ triggerKey: undefined })]);
    expect(other.trigger).toBe("其他");
  });
});

describe("filterRunRecords", () => {
  const merged = mergeRunRecords(
    [automationRecord()],
    [dutyRun({ summary: "巡检通过" })],
  );

  it("filters by status and searches names and summaries", () => {
    expect(filterRunRecords(merged, { status: "failed", query: "" })).toEqual([]);
    expect(filterRunRecords(merged, { status: "success", query: "巡检" }).map((item) => item.id))
      .toEqual(["execution_1"]);
    expect(filterRunRecords(merged, { status: "all", query: "PR" }).map((item) => item.id))
      .toEqual(["record_1"]);
  });
});
