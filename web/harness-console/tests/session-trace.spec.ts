import { describe, expect, it } from "vitest";
import {
  allFiltersEnabled,
  buildSessionTrace,
  filterTraceNodes,
  traceFilterKey,
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

  it("fills the staged-assets row with the manifest skill list", () => {
    const runs = extractSessionRuns([
      historyMessage({ id: "user-run-m", role: "user", content: "装载资产" }),
      historyMessage({
        id: "assistant-run-m",
        role: "assistant",
        content: "",
        toolCalls: [activityToolCall("run-m", runActivity("run-m", [
          {
            id: "staged-1",
            event_type: "agent.assets.staged",
            kind: "analysis",
            status: "succeeded",
            title: "Agent 资源已准备",
            summary: "已装载 2 个技能",
            timestamp: at(50),
            sequence: 1,
            metadata: { skill_count: 2 },
          },
        ]))],
      }),
    ]);
    const manifest = {
      systemPrompt: "p",
      entries: [
        { name: "skill-a", description: "A" },
        { name: "skill-b", description: "B" },
      ],
    };
    const trace = buildSessionTrace(runs, undefined, manifest);
    const staged = trace.nodes.find((node) => node.badge === "上下文");
    expect(staged?.entries?.map((entry) => entry.name)).toEqual(["skill-a", "skill-b"]);
    // Without a manifest the row stays count-only.
    const bare = buildSessionTrace(runs);
    expect(bare.nodes.find((node) => node.badge === "上下文")?.entries).toBeUndefined();
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
    ]);
  });

  it("attaches citations to tool nodes and aggregates run-level usage", () => {
    const runs = extractSessionRuns([
      historyMessage({ id: "user-run-k", role: "user", content: "检索知识" }),
      historyMessage({
        id: "assistant-run-k",
        role: "assistant",
        content: "",
        toolCalls: [activityToolCall("run-k", runActivity("run-k", [
          {
            id: "req-k",
            event_type: "tool.request",
            kind: "tool",
            status: "running",
            title: "search",
            summary: null,
            timestamp: at(100),
            sequence: 1,
            metadata: { tool_call_id: "c-k", name: "knowledge_search", arguments: { query: "档案" } },
          },
          {
            id: "res-k",
            event_type: "tool.result",
            kind: "tool",
            status: "succeeded",
            title: "search",
            summary: null,
            timestamp: at(500),
            sequence: 2,
            metadata: {
              tool_call_id: "c-k",
              result_preview: "hits",
              citations: [
                { index: 1, chunkId: "ck1", sourceReference: "doc/a.md", title: "档案规范", score: 0.91 },
                { index: 2, sourceReference: "doc/b.md" },
              ],
            },
          },
          {
            id: "final-k",
            event_type: "runtime.result",
            kind: "result",
            status: "succeeded",
            title: "模型执行完成",
            summary: null,
            timestamp: at(900),
            sequence: 3,
            metadata: { usage: { input_tokens: 1200, output_tokens: 340 } },
          },
        ]))],
      }),
    ]);
    const trace = buildSessionTrace(runs);
    const tool = trace.nodes.find((node) => node.badge === "工具");
    expect(tool?.citations).toHaveLength(2);
    expect(tool?.citations?.[0].title).toBe("档案规范");
    expect(trace.usage).toEqual({ inputTokens: 1200, outputTokens: 340, known: true });
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
    expect(trace.turns[1].left).toBeGreaterThanOrEqual(trace.turns[0].left + trace.turns[0].width);
    expect(trace.turns[1].left + trace.turns[1].width).toBeLessThanOrEqual(100.01);
  });
});

describe("filters", () => {
  it("groups badges and filters nodes by enabled toggles", () => {
    const trace = buildSessionTrace(extractSessionRuns(runOneHistory));
    const keys = new Set(trace.nodes.map((node) => traceFilterKey(node.badge)));
    expect(keys.has("system")).toBe(true);
    expect(keys.has("user")).toBe(true);
    const onlyTools = filterTraceNodes(trace.nodes, {
      ...allFiltersEnabled(),
      system: false,
      user: false,
      assistant: false,
      context: false,
    });
    expect(
      onlyTools.every((node) => ["工具", "子任务", "产物", "审批", "异常"].includes(node.badge)),
    ).toBe(true);
    expect(filterTraceNodes(trace.nodes, allFiltersEnabled())).toHaveLength(trace.nodes.length);
  });

  it("groups reasoning streams into 思考 rows separate from 助手 rows", () => {
    const runs = extractSessionRuns([
      historyMessage({ id: "user-run-t", role: "user", content: "带思考" }),
      historyMessage({
        id: "assistant-run-t",
        role: "assistant",
        content: "",
        toolCalls: [activityToolCall("run-t", runActivity("run-t", [
          ...deltaItems("m-9", [100, 200], "答案"),
          {
            id: "th-1",
            event_type: "reasoning.delta",
            kind: "analysis",
            status: "running",
            title: "思考",
            summary: "先分析",
            timestamp: at(50),
            sequence: 0,
            metadata: { message_id: "m-9" },
          },
        ]))],
      }),
    ]);
    const trace = buildSessionTrace(runs);
    const thinking = trace.nodes.filter((node) => node.badge === "思考");
    const answers = trace.nodes.filter((node) => node.badge === "助手");
    expect(thinking).toHaveLength(1);
    expect(thinking[0].output).toBe("先分析");
    expect(answers).toHaveLength(1);
    expect(answers[0].output).toBe("答案答案");
    expect(traceFilterKey("思考")).toBe("thinking");
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
  const turn = { turn: 1, runId: "run-1", startMs: 0, endMs: 1000, left: 0, width: 50 };

  it("maps node spans into the packed turn segment", () => {
    expect(timelinePosition({ startMs: 100, endMs: 200 }, turn)).toEqual({
      left: 5,
      width: 5,
    });
    expect(timelinePosition({ startMs: 500, endMs: 500 }, turn).width).toBeGreaterThan(0);
    expect(timelinePosition({ startMs: -50, endMs: 50 }, turn).left).toBe(0);
    // Clamped inside the turn segment.
    const beyond = timelinePosition({ startMs: 1_500, endMs: 1_800 }, turn);
    expect(beyond.left + beyond.width).toBeLessThanOrEqual(50.01);
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
