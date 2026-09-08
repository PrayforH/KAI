import { existsSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { runActivitySchema, type RunActivity } from "../src/lib/activity-schema";

function activity(
  status: string,
  items: Array<{
    id: string;
    event_type: string;
    kind?: "run" | "analysis" | "tool" | "subagent" | "artifact" | "result" | "error";
    status?: string;
    title?: string;
    summary?: string;
    sequence: number;
    metadata?: Record<string, unknown>;
  }>,
): RunActivity {
  return runActivitySchema.parse({
    run_id: "run-view",
    status,
    started_at: "2026-07-14T00:00:00Z",
    items: items.map((item) => ({
      kind: "run",
      status: "running",
      title: item.event_type,
      timestamp: `2026-07-14T00:00:0${item.sequence}Z`,
      metadata: {},
      ...item,
    })),
    metrics: {},
  });
}

async function moduleUnderTest() {
  expect(existsSync("src/lib/run-view-model.ts")).toBe(true);
  return import("../src/lib/run-view-model");
}

describe("run view model", () => {
  it("exposes the predecessor approval queue reason", async () => {
    const { reduceRunViewModel } = await moduleUnderTest();
    const view = reduceRunViewModel(
      undefined,
      activity("queued", [
        {
          id: "queued",
          event_type: "run.queued",
          status: "queued",
          title: "前序任务等待审批",
          summary: "前序任务等待审批",
          sequence: 1,
          metadata: {
            reason_code: "predecessor_waiting_approval",
            blocked_by_run_id: "run-original",
          },
        },
      ]),
    );

    expect(view.phase).toBe("queued");
    expect(view.queueReason).toBe("前序任务等待审批");
    expect(view.queueReasonCode).toBe("predecessor_waiting_approval");
    expect(view.blockedByRunId).toBe("run-original");
  });

  it("shows an actionable failure summary instead of the generic event title", async () => {
    const { reduceRunViewModel } = await moduleUnderTest();
    const view = reduceRunViewModel(
      undefined,
      activity("failed", [
        {
        id: "failed",
        event_type: "run.failed",
        kind: "error",
        status: "failed",
        title: "Agent 工具配置需要更新",
        summary: "当前版本绑定的 MCP 工具已变化，请切换到最新版本。",
        sequence: 1,
        metadata: { error_code: "runtime_error" },
        },
      ]),
    );

    expect(view.phase).toBe("failed");
    expect(view.summary).toBe(
      "当前版本绑定的 MCP 工具已变化，请切换到最新版本。",
    );
  });

  it("retains failure diagnostics and model-turn metrics", async () => {
    const { reduceRunViewModel } = await moduleUnderTest();
    const view = reduceRunViewModel(
      undefined,
      activity("failed", [
        {
          id: "runtime-result",
          event_type: "runtime.result",
          kind: "result",
          status: "failed",
          title: "达到最大模型回合数",
          summary: "已用完 19 个模型回合，任务尚未完成",
          sequence: 1,
          metadata: { turns: 19, stop_reason: "tool_use" },
        },
        {
          id: "failed",
          event_type: "run.failed",
          kind: "error",
          status: "failed",
          title: "达到最大执行回合数",
          sequence: 2,
          metadata: { error_code: "runtime_result_error" },
        },
      ]),
    );

    expect(view.turns).toBe(19);
    expect(view.stopReason).toBe("tool_use");
    expect(view.failureCode).toBe("runtime_result_error");
  });

  it("keeps terminal state monotonic when stale running activity arrives", async () => {
    const { reduceRunViewModel } = await moduleUnderTest();
    const running = reduceRunViewModel(
      undefined,
      activity("running", [
        { id: "run-start", event_type: "run.running", sequence: 1 },
      ]),
    );
    const completed = reduceRunViewModel(
      running,
      activity("succeeded", [
        { id: "run-finished", event_type: "run.succeeded", sequence: 2 },
      ]),
    );
    const afterStale = reduceRunViewModel(
      completed,
      activity("running", [
        { id: "stale", event_type: "run.running", sequence: 1 },
      ]),
    );

    expect(completed.phase).toBe("completed");
    expect(afterStale.phase).toBe("completed");
  });

  it("distinguishes model result from the durable run terminal", async () => {
    const { reduceRunViewModel } = await moduleUnderTest();
    const model = reduceRunViewModel(
      undefined,
      activity("succeeded", [
        {
          id: "runtime-result",
          event_type: "runtime.result",
          kind: "result",
          status: "succeeded",
          sequence: 2,
        },
      ]),
    );

    expect(model.phase).toBe("running");
  });

  it("keeps model routing as metadata instead of the active work summary", async () => {
    const { reduceRunViewModel } = await moduleUnderTest();
    const model = reduceRunViewModel(
      undefined,
      activity("running", [
        {
          id: "running",
          event_type: "run.running",
          status: "running",
          title: "Agent 开始执行",
          sequence: 1,
        },
        {
          id: "route",
          event_type: "model.route.selected",
          status: "succeeded",
          title: "模型路由已选择",
          summary: "deepseek-v4-pro",
          sequence: 2,
        },
      ]),
    );

    expect(model.summary).toBe("Agent 开始执行");
  });

  it("tracks pending approval and returns to running after a decision", async () => {
    const { reduceRunViewModel } = await moduleUnderTest();
    const waiting = reduceRunViewModel(
      undefined,
      activity("waiting", [
        {
          id: "approval-request",
          event_type: "approval.requested",
          kind: "tool",
          status: "waiting",
          sequence: 2,
          metadata: { approval_id: "approval-1", tool_call_id: "tool-1" },
        },
      ]),
    );
    const resumed = reduceRunViewModel(
      waiting,
      activity("running", [
        {
          id: "approval-rejected",
          event_type: "approval.rejected",
          kind: "tool",
          status: "failed",
          sequence: 3,
          metadata: { approval_id: "approval-1" },
        },
      ]),
    );

    expect(waiting.phase).toBe("waiting_approval");
    expect(waiting.pendingApprovalId).toBe("approval-1");
    expect(resumed.phase).toBe("running");
    expect(resumed.pendingApprovalId).toBeUndefined();
  });

  it("groups distinct subagent tasks and pairs tools with real results", async () => {
    const { reduceRunViewModel } = await moduleUnderTest();
    const model = reduceRunViewModel(
      undefined,
      activity("running", [
        {
          id: "task-start",
          event_type: "subagent.started",
          kind: "subagent",
          sequence: 1,
          metadata: { task_id: "task-1", parent_tool_use_id: "parent-1" },
        },
        {
          id: "tool-start",
          event_type: "tool.request",
          kind: "tool",
          sequence: 2,
          metadata: {
            tool_call_id: "tool-1",
            name: "Read",
            arguments: { file_path: "README.md" },
          },
        },
        {
          id: "tool-allowed",
          event_type: "tool.allowed",
          kind: "tool",
          sequence: 3,
          metadata: { tool_call_id: "tool-1" },
        },
        {
          id: "tool-result",
          event_type: "tool.result",
          kind: "tool",
          status: "succeeded",
          sequence: 4,
          metadata: {
            tool_call_id: "tool-1",
            result_summary: "返回 12 行 · 480 字符",
            result_preview: "first line\nsecond line",
          },
        },
      ]),
    );

    expect(model.tasks).toHaveLength(1);
    expect(model.tasks[0]).toMatchObject({ id: "task-1", parentId: "parent-1" });
    expect(model.tools).toEqual([
      expect.objectContaining({
        id: "tool-1",
        name: "Read",
        status: "completed",
        arguments: { file_path: "README.md" },
        resultSummary: "返回 12 行 · 480 字符",
        resultPreview: "first line\nsecond line",
      }),
    ]);
    expect(model.toolCount).toBe(1);
    expect(model.taskCount).toBe(1);
  });

  it("merges a delegation tool request into its real SDK subagent task", async () => {
    const { reduceRunViewModel } = await moduleUnderTest();
    const model = reduceRunViewModel(
      undefined,
      activity("succeeded", [
        {
          id: "delegate-request",
          event_type: "tool.request",
          kind: "subagent",
          status: "running",
          title: "调用 Agent",
          sequence: 1,
          metadata: { tool_call_id: "call-1", name: "Agent" },
        },
        {
          id: "task-started",
          event_type: "subagent.started",
          kind: "subagent",
          status: "running",
          title: "子 Agent 正在执行",
          sequence: 2,
          metadata: { task_id: "task-1", parent_tool_use_id: "call-1" },
        },
        {
          id: "task-completed",
          event_type: "subagent.completed",
          kind: "subagent",
          status: "succeeded",
          title: "子 Agent 已完成",
          summary: "证据检查完成",
          sequence: 3,
          metadata: { task_id: "task-1", parent_tool_use_id: "call-1" },
        },
      ]),
    );

    expect(model.taskCount).toBe(1);
    expect(model.tasks).toEqual([
      expect.objectContaining({
        id: "task-1",
        parentId: "call-1",
        title: "证据检查完成",
        status: "completed",
      }),
    ]);
  });

  it("derives composer locking from the same run phase", async () => {
    const { selectComposerDisabled } = await moduleUnderTest();

    expect(selectComposerDisabled(undefined)).toBe(false);
    expect(selectComposerDisabled({ phase: "running" })).toBe(true);
    expect(selectComposerDisabled({ phase: "waiting_approval" })).toBe(true);
    expect(selectComposerDisabled({ phase: "completed" })).toBe(false);
    expect(selectComposerDisabled({ phase: "failed" })).toBe(false);
  });
});


describe("denied delegation", () => {
  it("finishes an Agent attempt from tool.result without a child lifecycle", async () => {
    const { reduceRunViewModel } = await moduleUnderTest();
    const view = reduceRunViewModel(undefined, activity("failed", [
      { id: "request", event_type: "tool.request", kind: "subagent", sequence: 1,
        title: "调用 Agent", metadata: { tool_call_id: "denied-call", name: "Agent" } },
      { id: "allowed", event_type: "tool.allowed", kind: "tool", status: "succeeded", sequence: 2,
        metadata: { tool_call_id: "denied-call" } },
      { id: "result", event_type: "tool.result", kind: "tool", status: "failed", sequence: 3,
        metadata: { tool_call_id: "denied-call", result_preview: "subagent role is not declared" } },
      { id: "failed", event_type: "run.failed", status: "failed", sequence: 4 },
    ]));
    expect(view.tasks).toHaveLength(1);
    expect(view.tasks[0].status).toBe("failed");
    expect(view.tasks[0].title).toContain("subagent role is not declared");
    expect(view.tools).toHaveLength(0);
  });
});
