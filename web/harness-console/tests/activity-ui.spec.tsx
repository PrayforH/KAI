import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ActivitySummary } from "../src/components/activity-summary";
import { ApprovalCard } from "../src/components/approval-card";
import { ArtifactCard } from "../src/components/artifact-list";
import { SubagentCard } from "../src/components/subagent-card";
import { ToolCard } from "../src/components/tool-card";
import { completedToolBatch } from "../src/components/tool-card";
import type { RunViewModel } from "../src/lib/run-view-model";
import {
  hasProjectedTool,
  selectTurnActivity,
  shouldCaptureTurnActivity,
  shouldKeepActivityInLatestSlot,
  shouldShowArtifactForTurn,
  shouldSuppressRawToolCard,
  UploadFeedbackContent,
} from "../src/components/agent-thread";
import {
  activityOverview,
  hasRunActivityToolCall,
  latestHistoryRunActivity,
  latestRunActivity,
  runActivitySchema,
} from "../src/lib/activity-schema";

const activity = {
  run_id: "run-1",
  status: "running",
  started_at: "2026-07-13T01:00:00Z",
  metrics: {},
  items: [
    {
      id: "event-1",
      event_type: "model.route.selected",
      kind: "run",
      status: "succeeded",
      title: "模型路由已选择",
      summary: "claude-sonnet",
      timestamp: "2026-07-13T01:00:01Z",
      sequence: 1,
      metadata: { provider: "new-api", model: "claude-sonnet" },
    },
    {
      id: "event-2",
      event_type: "tool.request",
      kind: "tool",
      status: "running",
      title: "调用 Read",
      timestamp: "2026-07-13T01:00:02Z",
      sequence: 2,
      metadata: {
        name: "Read",
        arguments: { file_path: "docs/agent-production-platform-design.md" },
      },
    },
    {
      id: "event-3",
      event_type: "subagent.started",
      kind: "subagent",
      status: "running",
      title: "子 Agent 正在执行",
      summary: "分析仓库",
      timestamp: "2026-07-13T01:00:03Z",
      sequence: 3,
      metadata: { task_id: "task-1" },
    },
  ],
};

describe("Codex-style activity UI", () => {
  it("validates the durable activity payload", () => {
    expect(runActivitySchema.parse(activity).items).toHaveLength(3);
  });

  it("finds the latest activity and derives inspector facts", () => {
    const completed = runActivitySchema.parse({
      ...activity,
      status: "succeeded",
      metrics: { turns: 3, cost_usd: 0.0125, stop_reason: "end_turn" },
    });
    const found = latestRunActivity([
      { role: "user", content: "hello" },
      { role: "activity", activityType: "harness.run.v1", content: completed },
    ]);
    expect(found?.run_id).toBe("run-1");
    expect(activityOverview(completed)).toMatchObject({
      model: "claude-sonnet",
      provider: "new-api",
      turns: "3",
      cost: "$0.0125",
      stopReason: "end_turn",
    });
  });

  it("restores the latest activity from durable history tool calls", () => {
    const completed = runActivitySchema.parse({
      ...activity,
      status: "succeeded",
    });
    const found = latestHistoryRunActivity([
      {
        role: "assistant",
        content: "done",
        toolCalls: [
          {
            function: {
              name: "harness_run_activity",
              arguments: JSON.stringify({ activity: completed }),
            },
          },
        ],
      },
      { role: "tool", content: '{"status":"ready"}' },
    ]);

    expect(found?.run_id).toBe("run-1");
    expect(found?.status).toBe("succeeded");
  });

  it("detects a durable activity card so the live summary is not duplicated", () => {
    expect(
      hasRunActivityToolCall([
        { type: "text", text: "answer" },
        {
          type: "tool-call",
          toolName: "harness_run_activity",
          toolCallId: "activity-1",
        },
      ]),
    ).toBe(true);
    expect(
      hasRunActivityToolCall([
        { type: "tool-call", toolName: "Read", toolCallId: "read-1" },
      ]),
    ).toBe(false);
  });

  it("renders tools and subagents in one flat Codex transcript", () => {
    const html = renderToStaticMarkup(
      <ActivitySummary activity={runActivitySchema.parse(activity)} />,
    );
    expect(html).toContain("执行进度");
    expect(html).toContain("正在读取 docs/agent-production-platform-design.md");
    expect(html).toContain('aria-label="运行过程，仅展示可观察事件"');
    expect(html).toContain("正在运行子任务 分析仓库");
    expect(html).not.toContain("运行模型");
    expect(html).not.toContain("claude-sonnet");
    expect(html.match(/<section class="execution-ribbon/g)).toHaveLength(1);
    expect(html).not.toContain("model.route.selected");
  });

  it("renders rich tool input and result cards", () => {
    const html = renderToStaticMarkup(
      <ToolCard name="Read" status="complete" args={{ file_path: "README.md" }} result={'{"ok":true}'} />,
    );
    expect(html).toContain("读取文件");
    expect(html).toContain("Read");
    expect(html).toContain("已完成");
    expect(html).toContain("file_path");
    expect(html).toContain("json-boolean");
  });

  it("keeps ordinary tool cards as a fallback when no activity projection exists", () => {
    const html = renderToStaticMarkup(
      <ToolCard
        toolCallId="read-without-activity"
        name="Read"
        status="complete"
        args={{ file_path: "README.md" }}
        result="fallback"
      />,
    );

    expect(html).toContain("读取文件");
    expect(html).toContain("README.md");
  });

  it("deduplicates ordinary tool cards already represented by the turn activity", () => {
    const view = {
      tools: [
        { id: "read-projected", name: "Read", status: "completed", sequence: 1 },
      ],
    } as RunViewModel;

    expect(hasProjectedTool(view, "read-projected")).toBe(true);
    expect(hasProjectedTool(view, "read-fallback")).toBe(false);
  });

  it("suppresses stale raw SDK tool cards whenever durable run activity exists", () => {
    const view = {
      runId: "run-failed",
      tools: [],
    } as unknown as RunViewModel;

    expect(shouldSuppressRawToolCard(view, "stale-bash-call")).toBe(true);
    expect(shouldSuppressRawToolCard(undefined, "standalone-tool")).toBe(false);
  });

  it("keeps activity by durable Harness identity without comparing AG-UI client ids", () => {
    expect(shouldKeepActivityInLatestSlot("run-1", "run-1")).toBe(true);
    expect(shouldKeepActivityInLatestSlot("run-1", "run-2")).toBe(false);
    expect(shouldKeepActivityInLatestSlot("run-1", undefined)).toBe(false);
  });

  it("does not attach an older run artifact to the latest turn", () => {
    expect(shouldShowArtifactForTurn("run-1", "run-2", true)).toBe(false);
    expect(shouldShowArtifactForTurn("run-2", "run-2", true)).toBe(true);
    expect(shouldShowArtifactForTurn("run-1", "run-2", false)).toBe(true);
  });

  it("retains a live activity snapshot on its original turn after a newer turn starts", () => {
    const completed = runActivitySchema.parse({
      ...activity,
      status: "succeeded",
    });
    const newer = runActivitySchema.parse({
      ...activity,
      run_id: "run-2",
    });

    expect(selectTurnActivity(newer, completed, false, false)?.run_id).toBe(
      "run-1",
    );
    expect(selectTurnActivity(newer, completed, true, false)?.run_id).toBe(
      "run-2",
    );
    expect(selectTurnActivity(newer, completed, false, true)).toBeUndefined();
    expect(selectTurnActivity(newer, completed, true, true)?.run_id).toBe(
      "run-2",
    );
  });

  it("captures a batched terminal delta after the turn stops being last", () => {
    expect(
      shouldCaptureTurnActivity("run-1", "run-1", false, "run-1"),
    ).toBe(true);
    expect(
      shouldCaptureTurnActivity("run-2", "run-1", false, "run-2"),
    ).toBe(false);
    expect(
      shouldCaptureTurnActivity("run-2", "run-1", true, "run-2"),
    ).toBe(true);
    expect(
      shouldCaptureTurnActivity("run-1", "run-1", false, "run-2"),
    ).toBe(false);
  });

  it("selects multiple completed ordinary tools for one collapsed batch", () => {
    const tools = completedToolBatch({
      tools: [
        { id: "tool-1", name: "tool", status: "completed", sequence: 1 },
        { id: "tool-2", name: "Bash", status: "completed", sequence: 2 },
        { id: "tool-3", name: "Edit", status: "failed", sequence: 3 },
        { id: "tool-4", name: "harness_request_approval", status: "completed", sequence: 4 },
      ],
    } as RunViewModel);

    expect(tools.map((tool) => tool.id)).toEqual(["tool-1", "tool-2"]);
  });

  it("presents Task calls as delegated subagents", () => {
    const html = renderToStaticMarkup(
      <SubagentCard
        status="executing"
        parameters={{ description: "分析仓库", subagent_type: "helper" }}
      />,
    );
    expect(html).toContain("委派给 helper");
    expect(html).toContain("helper");
    expect(html).toContain("分析仓库");
  });

  it("renders upload progress and actionable errors", () => {
    const html = renderToStaticMarkup(
      <UploadFeedbackContent
        items={[
          { key: "a", fileName: "facts.txt", status: "uploading" },
          { key: "b", fileName: "report.docx", status: "error", message: "too large" },
        ]}
        onDismiss={() => undefined}
      />,
    );
    expect(html).toContain("正在上传");
    expect(html).toContain("上传失败：too large");
    expect(html).toContain("关闭 report.docx 上传错误");
  });

  it("renders authenticated preview and download actions for artifacts", () => {
    const html = renderToStaticMarkup(
      <ArtifactCard
        details={{
          artifact_id: "artifact-1",
          run_id: "run-1",
          name: "report.pdf",
          media_type: "application/pdf",
          size_bytes: 2048,
        }}
      />,
    );
    expect(html).toContain("PDF");
    expect(html).toContain("预览 report.pdf");
    expect(html).toContain("?preview=1");
    expect(html).toContain("artifact-primary-link");
    expect(html).toContain("点击预览 report.pdf");
    expect(html).not.toContain("artifact-actions");
  });

  it("renders actionable controls for a pending inline approval", () => {
    const html = renderToStaticMarkup(
      <ApprovalCard
        details={{
          approval_id: "approval-1",
          run_id: "run-1",
          tool_call_id: "tool-1",
          reason: "matched policy rule write-review",
          risk: "high",
        }}
        complete={false}
        onDecision={async () => undefined}
      />,
    );

    expect(html).toContain("允许执行这个操作？");
    expect(html).toContain("允许并继续");
    expect(html).toContain("高风险");
    expect(html).toContain("拒绝");
    expect(html).toContain("write-review");
  });
});
