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

function runActivity(runId: string, items: Record<string, unknown>[]) {
  return {
    run_id: runId,
    trace_id: null,
    status: "succeeded",
    started_at: at(0),
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
    const input = trace.nodes.find((node) => node.lane === "input");
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
