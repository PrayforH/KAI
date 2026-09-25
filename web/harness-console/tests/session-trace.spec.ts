import { describe, expect, it } from "vitest";
import {
  buildSessionTrace,
  extractSessionRuns,
  formatClock,
  formatDuration,
  mergeLiveRun,
  searchTraceNodes,
  timelinePosition,
} from "../src/lib/session-trace";

const T0 = Date.parse("2026-09-25T03:00:00.000Z");
const at = (offsetMs: number) => new Date(T0 + offsetMs).toISOString();

function historyMessage(payload: Record<string, unknown>) {
  return payload;
}

function activityToolCall(runId: string, activity: Record<string, unknown>) {
  return {
    id: `harness-activity-${runId}`,
    function: {
      name: "harness_run_activity",
      arguments: JSON.stringify({ activity }),
    },
  };
}

function runActivity(runId: string, items: Record<string, unknown>[], startOffset = 0) {
  return {
    run_id: runId,
    trace_id: null,
    status: "succeeded",
    started_at: at(startOffset),
    items,
    metrics: {},
  };
}

function toolPair(
  runId: string,
  callId: string,
  name: string,
  startOffset: number,
  endOffset: number,
  resultPreview = "ok",
) {
  return [
    {
      id: `${runId}-req-${callId}`,
      event_type: "tool.request",
      kind: "tool",
      status: "running",
      title: name,
      summary: null,
      timestamp: at(startOffset),
      sequence: startOffset,
      metadata: { tool_call_id: callId, name, arguments: { command: "ls -la" } },
    },
    {
      id: `${runId}-res-${callId}`,
      event_type: "tool.result",
      kind: "tool",
      status: "succeeded",
      title: name,
      summary: null,
      timestamp: at(endOffset),
      sequence: endOffset,
      metadata: { tool_call_id: callId, result_preview: resultPreview },
    },
  ];
}

function deltaItems(messageId: string, offsets: number[], text: string) {
  return offsets.map((offset) => ({
    id: `delta-${messageId}-${offset}`,
    event_type: "message.delta",
    kind: "analysis",
    status: "running",
    title: "回答",
    summary: text,
    timestamp: at(offset),
    sequence: offset,
    metadata: { message_id: messageId },
  }));
}

const runOneHistory = [
  historyMessage({ id: "user-run-1", role: "user", content: "整理目录并汇报" }),
  historyMessage({
    id: "assistant-run-1",
    role: "assistant",
    content: "完成。",
    toolCalls: [
      activityToolCall("run-1", runActivity("run-1", [
        ...deltaItems("m-1", [100, 300], "你好"),
        ...toolPair("run-1", "c-1", "Bash", 400, 1500, "total 0"),
        {
          id: "run-1-art",
          event_type: "artifact.ready",
          kind: "artifact",
          status: "succeeded",
          title: "产物",
          summary: "report.md",
          timestamp: at(2000),
          sequence: 2000,
          metadata: { artifact_id: "art-1", source_path: "report.md" },
        },
      ])),
    ],
  }),
];

describe("extractSessionRuns", () => {
  it("extracts ordered runs with prompts and renumbers turns", () => {
    const secondRun = [
      historyMessage({ id: "user-run-2", role: "user", content: "再来一轮" }),
      historyMessage({
        id: "assistant-run-2",
        role: "assistant",
        content: "好",
        toolCalls: [activityToolCall("run-2", runActivity("run-2", []))],
      }),
    ];
    const runs = extractSessionRuns([...runOneHistory, ...secondRun]);
    expect(runs.map((run) => [run.runId, run.turn])).toEqual([
      ["run-1", 1],
      ["run-2", 2],
    ]);
    expect(runs[0].prompt).toBe("整理目录并汇报");
  });

  it("deduplicates runs across overlapping history pages", () => {
    const runs = extractSessionRuns([...runOneHistory, ...runOneHistory]);
    expect(runs).toHaveLength(1);
  });

  it("ignores malformed activity payloads", () => {
    const broken = [
      historyMessage({ id: "user-run-x", role: "user", content: "坏数据" }),
      historyMessage({
        id: "assistant-run-x",
        role: "assistant",
        content: "",
        toolCalls: [
          {
            id: "harness-activity-run-x",
            function: { name: "harness_run_activity", arguments: "{not json" },
          },
        ],
      }),
    ];
    expect(extractSessionRuns(broken)).toHaveLength(0);
  });
});

describe("buildSessionTrace", () => {
  it("pairs tool requests with results and computes durations", () => {
    const trace = buildSessionTrace(extractSessionRuns(runOneHistory));
    const tool = trace.nodes.find((node) => node.badge === "工具");
    expect(tool).toBeDefined();
    expect(tool?.label).toBe("Bash");
    expect(tool ? tool.endMs - tool.startMs : 0).toBe(1100);
    expect(tool?.output).toBe("total 0");
    expect(tool?.argumentsText).toContain("ls -la");
    expect(trace.summary.toolCalls).toBe(1);
  });

  it("builds model spans from streaming deltas and input nodes from prompts", () => {
    const trace = buildSessionTrace(extractSessionRuns(runOneHistory));
    const model = trace.nodes.filter((node) => node.lane === "model");
    expect(model).toHaveLength(1);
    expect(model[0].startMs).toBe(T0 + 100);
    expect(model[0].endMs).toBe(T0 + 300);
    expect(model[0].output).toBe("你好你好");
    const input = trace.nodes.find((node) => node.badge === "用户");
    expect(input?.badge).toBe("用户");
    expect(input?.output).toBe("整理目录并汇报");
  });

  it("summarises window, turns and tool calls", () => {
    const trace = buildSessionTrace(extractSessionRuns(runOneHistory));
    expect(trace.summary.turns).toBe(1);
    expect(trace.window.startMs).toBe(T0);
    expect(trace.window.endMs).toBe(T0 + 2000);
  });

  it("merges a live run that history has not persisted yet", () => {
    const runs = extractSessionRuns(runOneHistory);
    const live = runActivity("run-live", [
      ...toolPair("run-live", "c-9", "Grep", 0, 50),
    ]) as never;
    const merged = mergeLiveRun(runs, live);
    expect(merged.at(-1)?.turn).toBe(2);
    const trace = buildSessionTrace(runs, live);
    expect(trace.summary.turns).toBe(2);
  });

  it("pairs subagent milestones into one span and ignores progress frames", () => {
    const runs = extractSessionRuns([
      historyMessage({ id: "user-run-s", role: "user", content: "委派子任务" }),
      historyMessage({
        id: "assistant-run-s",
        role: "assistant",
        content: "",
        toolCalls: [activityToolCall("run-s", runActivity("run-s", [
          {
            id: "sa-start",
            event_type: "subagent.started",
            kind: "subagent",
            status: "running",
            title: "检索子任务",
            summary: null,
            timestamp: at(100),
            sequence: 1,
            metadata: { task_id: "t-1" },
          },
          {
            id: "sa-progress-1",
            event_type: "subagent.progress",
            kind: "subagent",
            status: "running",
            title: "检索子任务",
            summary: "进行中",
            timestamp: at(200),
            sequence: 2,
            metadata: { task_id: "t-1" },
          },
          {
            id: "sa-done",
            event_type: "subagent.completed",
            kind: "subagent",
            status: "succeeded",
            title: "检索子任务",
            summary: "完成",
            timestamp: at(900),
            sequence: 3,
            metadata: { task_id: "t-1" },
          },
        ]))],
      }),
    ]);
    const trace = buildSessionTrace(runs);
    const subagents = trace.nodes.filter((node) => node.badge === "子任务");
    expect(subagents).toHaveLength(1);
    expect(subagents[0].endMs - subagents[0].startMs).toBe(800);
    expect(subagents[0].status).toBe("succeeded");
  });

  it("folds approval lifecycle into the paired tool node", () => {
    const runs = extractSessionRuns([
      historyMessage({ id: "user-run-a", role: "user", content: "需要审批的任务" }),
      historyMessage({
        id: "assistant-run-a",
        role: "assistant",
        content: "",
        toolCalls: [activityToolCall("run-a", runActivity("run-a", [
          {
            id: "req-1",
            event_type: "tool.request",
            kind: "tool",
            status: "running",
            title: "Bash",
            summary: null,
            timestamp: at(100),
            sequence: 1,
            metadata: {
              tool_call_id: "c-7",
              name: "Bash",
              arguments: { command: "rm draft.txt" },
            },
          },
          {
            id: "appr-1",
            event_type: "approval.requested",
            kind: "tool",
            status: "waiting",
            title: "等待人工审批",
            summary: "删除文件需要确认",
            timestamp: at(150),
            sequence: 2,
            metadata: { approval_id: "ap-1", tool_call_id: "c-7" },
          },
          {
            id: "appr-done",
            event_type: "approval.approved",
            kind: "tool",
            status: "succeeded",
            title: "审批已通过",
            summary: null,
            timestamp: at(600),
            sequence: 3,
            metadata: { approval_id: "ap-1" },
          },
          {
            id: "res-1",
            event_type: "tool.result",
            kind: "tool",
            status: "succeeded",
            title: "Bash",
            summary: null,
            timestamp: at(800),
            sequence: 4,
            metadata: { tool_call_id: "c-7", result_preview: "deleted" },
          },
        ]))],
      }),
    ]);
    const trace = buildSessionTrace(runs);
    const toolNodes = trace.nodes.filter((node) => node.badge === "工具");
    const approvalNodes = trace.nodes.filter((node) => node.badge === "审批");
    expect(approvalNodes).toHaveLength(0);
    expect(toolNodes).toHaveLength(1);
    expect(toolNodes[0].status).toBe("succeeded");
    expect(toolNodes[0].endMs - toolNodes[0].startMs).toBe(700);
    expect(toolNodes[0].detail).not.toContain("待审批");
  });

  it("marks a tool as waiting approval until the approval settles", () => {
    const runs = extractSessionRuns([
      historyMessage({ id: "user-run-w", role: "user", content: "等待审批" }),
      historyMessage({
        id: "assistant-run-w",
        role: "assistant",
        content: "",
        toolCalls: [activityToolCall("run-w", runActivity("run-w", [
          {
            id: "req-w",
            event_type: "tool.request",
            kind: "tool",
            status: "running",
            title: "Write",
            summary: null,
            timestamp: at(100),
            sequence: 1,
            metadata: { tool_call_id: "c-8", name: "Write", arguments: { file_path: "a.md" } },
          },
          {
            id: "appr-w",
            event_type: "approval.requested",
            kind: "tool",
            status: "waiting",
            title: "等待人工审批",
            summary: null,
            timestamp: at(150),
            sequence: 2,
            metadata: { approval_id: "ap-8", tool_call_id: "c-8" },
          },
        ]))],
      }),
    ]);
    const trace = buildSessionTrace(runs);
    const tool = trace.nodes.find((node) => node.badge === "工具");
    expect(tool?.running).toBe(true);
    expect(tool?.status).toBe("waiting");
    expect(tool?.detail).toContain("待审批");
  });

  it("keeps a standalone approval node when no tool call carries it", () => {
    const runs = extractSessionRuns([
      historyMessage({ id: "user-run-p", role: "user", content: "独立审批" }),
      historyMessage({
        id: "assistant-run-p",
        role: "assistant",
        content: "",
        toolCalls: [activityToolCall("run-p", runActivity("run-p", [
          {
            id: "appr-p",
            event_type: "approval.requested",
            kind: "tool",
            status: "waiting",
            title: "等待人工审批",
            summary: "敏感操作",
            timestamp: at(100),
            sequence: 1,
            metadata: { approval_id: "ap-p", tool_call_id: "ghost-1" },
          },
          {
            id: "appr-p-rej",
            event_type: "approval.rejected",
            kind: "tool",
            status: "failed",
            title: "审批已拒绝",
            summary: null,
            timestamp: at(400),
            sequence: 2,
            metadata: { approval_id: "ap-p" },
          },
        ]))],
      }),
    ]);
    const trace = buildSessionTrace(runs);
    const approvals = trace.nodes.filter((node) => node.badge === "审批");
    expect(approvals).toHaveLength(1);
    expect(approvals[0].status).toBe("failed");
    expect(approvals[0].endMs - approvals[0].startMs).toBe(300);
  });

  it("renders one system node with the versioned prompt and tool list", () => {
    const runs = extractSessionRuns(runOneHistory);
    const trace = buildSessionTrace(runs, undefined, {
      systemPrompt: "你是档案助手。",
      entries: [{ name: "archive-policy", description: "档案分类规范" }],
    });
    const system = trace.nodes.filter((node) => node.badge === "系统");
    expect(system).toHaveLength(1);
    expect(system[0].label).toBe("初始系统提示词");
    expect(system[0].systemPrompt).toBe("你是档案助手。");
    expect(system[0].entries?.[0].name).toBe("archive-policy");
    expect(trace.nodes[0].badge).toBe("系统");
  });

  it("falls back to runtime facts when no manifest resolves", () => {
    const runs = extractSessionRuns(runOneHistory);
    const trace = buildSessionTrace(runs);
    const system = trace.nodes.filter((node) => node.badge === "系统");
    expect(system).toHaveLength(1);
    expect(system[0].label).toBe("系统与运行时");
    expect(system[0].systemPrompt).toBeUndefined();
  });

  it("maps runtime framing events to 上下文 nodes", () => {
    const runs = extractSessionRuns([
      historyMessage({ id: "user-run-c", role: "user", content: "带上下文事件" }),
      historyMessage({
        id: "assistant-run-c",
        role: "assistant",
        content: "",
        toolCalls: [activityToolCall("run-c", runActivity("run-c", [
          {
            id: "ctx-1",
            event_type: "policy.resolved",
            kind: "analysis",
            status: "succeeded",
            title: "运行权限已确认",
            summary: "默认策略",
            timestamp: at(50),
            sequence: 1,
            metadata: {},
          },
          {
            id: "ctx-2",
            event_type: "runtime.system",
            kind: "analysis",
            status: "running",
            title: "模型正在处理",
            summary: null,
            timestamp: at(60),
            sequence: 2,
            metadata: {},
          },
        ]))],
      }),
    ]);
    const trace = buildSessionTrace(runs);
    const contexts = trace.nodes.filter((node) => node.badge === "上下文");
    expect(contexts.map((node) => node.label)).toEqual([
      "运行权限已确认",
      "模型正在处理",
    ]);
  });

  it("computes per-turn windows for the timeline axis", () => {
    const secondRun = [
      historyMessage({ id: "user-run-2", role: "user", content: "再来一轮" }),
      historyMessage({
        id: "assistant-run-2",
        role: "assistant",
        content: "",
        toolCalls: [activityToolCall("run-2", runActivity("run-2", [
          ...deltaItems("m-2", [5_000, 5_400], "第二轮回答"),
        ], 5_000))],
      }),
    ];
    const trace = buildSessionTrace(extractSessionRuns([...runOneHistory, ...secondRun]));
    expect(trace.turns).toHaveLength(2);
    expect(trace.turns[0].left).toBe(0);
    expect(trace.turns[1].left).toBeGreaterThan(trace.turns[0].left);
    expect(trace.turns[1].left + trace.turns[1].width).toBeLessThanOrEqual(100.01);
  });
});

describe("searchTraceNodes", () => {
  const trace = buildSessionTrace(extractSessionRuns(runOneHistory));

  it("matches label, detail and output case-insensitively", () => {
    expect(searchTraceNodes(trace.nodes, "bash")).toHaveLength(1);
    expect(searchTraceNodes(trace.nodes, "整理")).toHaveLength(1);
    expect(searchTraceNodes(trace.nodes, "不存在")).toHaveLength(0);
    expect(searchTraceNodes(trace.nodes, "  ")).toHaveLength(trace.nodes.length);
  });
});

describe("timelinePosition", () => {
  const window = { startMs: 0, endMs: 1000, totalMs: 1000 };

  it("maps node spans to percentages with a minimum width", () => {
    expect(timelinePosition({ startMs: 100, endMs: 200 }, window)).toEqual({
      left: 10,
      width: 10,
    });
    expect(timelinePosition({ startMs: 500, endMs: 500 }, window).width).toBeGreaterThan(0);
    expect(timelinePosition({ startMs: -50, endMs: 50 }, window).left).toBe(0);
  });
});

describe("formatting", () => {
  it("formats durations and local clocks", () => {
    expect(formatDuration(730)).toBe("730ms");
    expect(formatDuration(7_360)).toBe("7.4s");
    expect(formatDuration(125_000)).toBe("2m 05s");
    expect(formatClock(Number.NaN)).toBe("—");
  });
});
