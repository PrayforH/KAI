import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { RunTraceConsole } from "../src/components/run-trace-console";
import { extractSessionRuns } from "../src/lib/session-trace";

const T0 = Date.parse("2026-09-25T03:00:00.000Z");
const at = (offsetMs: number) => new Date(T0 + offsetMs).toISOString();

const history = [
  {
    id: "user-run-1",
    role: "user",
    content: "整理目录并汇报",
  },
  {
    id: "assistant-run-1",
    role: "assistant",
    content: "完成。",
    toolCalls: [
      {
        id: "harness-activity-run-1",
        function: {
          name: "harness_run_activity",
          arguments: JSON.stringify({
            activity: {
              run_id: "run-1",
              trace_id: null,
              status: "succeeded",
              started_at: at(0),
              items: [
                {
                  id: "e-1",
                  event_type: "message.delta",
                  kind: "analysis",
                  status: "succeeded",
                  title: "回答",
                  summary: "开始整理",
                  timestamp: at(100),
                  sequence: 1,
                  metadata: { message_id: "m-1" },
                },
                {
                  id: "e-2",
                  event_type: "tool.request",
                  kind: "tool",
                  status: "running",
                  title: "Bash",
                  summary: null,
                  timestamp: at(200),
                  sequence: 2,
                  metadata: {
                    tool_call_id: "c-1",
                    name: "Bash",
                    arguments: { command: "ls" },
                  },
                },
                {
                  id: "e-3",
                  event_type: "tool.result",
                  kind: "tool",
                  status: "succeeded",
                  title: "Bash",
                  summary: null,
                  timestamp: at(900),
                  sequence: 3,
                  metadata: { tool_call_id: "c-1", result_preview: "total 0" },
                },
              ],
              metrics: {},
            },
          }),
        },
      },
    ],
  },
];

// The console fetches history on mount; renderToStaticMarkup cannot await that,
// so the loading skeleton and empty states are the server-render contract.
describe("RunTraceConsole", () => {
  it("renders a stable loading shell before the full projection is ready", () => {
    const html = renderToStaticMarkup(
      <RunTraceConsole threadId="thread-1" liveActivity={null} />,
    );
    expect(html).toContain("正在读取完整轨迹");
    expect(html).toContain('aria-busy="true"');
  });

  it("keeps the event shell accessible while the full projection loads", () => {
    const html = renderToStaticMarkup(
      <RunTraceConsole threadId="thread-1" liveActivity={null} />,
    );
    expect(html).toContain("调用轨迹");
    expect(html).toContain("正在读取完整轨迹");
  });

  it("extracts nodes the console will display once history arrives", () => {
    const trace = extractSessionRuns(history);
    expect(trace).toHaveLength(1);
    expect(trace[0].prompt).toBe("整理目录并汇报");
  });
});
