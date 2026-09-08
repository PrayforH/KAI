import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { developerRows } from "../src/lib/developer-details";
import {
  notableActivityItems,
  traceActivityEntries,
} from "../src/components/developer-drawer";
import type { ActivityItem } from "../src/lib/activity-schema";

const panel = readFileSync(
  join(process.cwd(), "src/components/developer-drawer.tsx"),
  "utf8",
);
const styles = readFileSync(join(process.cwd(), "src/app/styles.css"), "utf8");
const codexTheme = readFileSync(
  join(process.cwd(), "src/app/codex-theme.css"),
  "utf8",
);

describe("developer drawer", () => {
  it("shows protocol coordinates without leaking server identity", () => {
    const rows = developerRows("thread-1");

    expect(rows).toEqual([
      ["THREAD", "thread-1"],
      ["AGENT", "harness-agent"],
      ["ROUTE", "/api/agui → Harness AG-UI"],
    ]);
    expect(JSON.stringify(rows)).not.toContain("X-Tenant-ID");
    expect(JSON.stringify(rows)).not.toContain("developer");
  });

  it("keeps the overview concise without exposing the local event timeline", () => {
    expect(panel).toContain("运行详情");
    expect(panel).toContain("本次运行");
    expect(panel).toContain("打开 Trace");
    expect(panel).not.toContain("完整执行 Trace");
    expect(panel).not.toContain("Trace · {traceEntries.length} 个步骤");
    expect(panel).not.toContain('className="trace-ledger"');
    expect(panel).toContain("Trace 尚未生成");
    expect(panel).toContain("还没有运行记录");
    expect(panel).not.toContain("inspector-title-mark");
    expect(panel).not.toContain("observability-mark");
    expect(panel).not.toContain("empty-orbit");
    expect(panel).not.toContain("高级诊断");
    expect(panel).not.toContain("Harness activity");
    expect(panel).not.toContain("Run inspector");
    expect(panel).not.toContain("协议与原始事件");
    expect(panel).not.toContain("条原始事件");
  });

  it("uses a compact metric grid and quiet observability row", () => {
    expect(panel).toContain('className="run-metric-wide"');
    expect(codexTheme).toMatch(
      /body\.codex-theme-v1 \.developer-drawer \.run-metrics\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\);/s,
    );
    expect(codexTheme).toMatch(
      /body\.codex-theme-v1 \.observability-link\s*\{[^}]*min-height:\s*52px;/s,
    );
    expect(codexTheme).toMatch(
      /body\.codex-theme-v1 \.developer-drawer \.run-counts span\s*\{[^}]*background:\s*transparent;/s,
    );
  });

  it("keeps identifiers collapsed and links the current run to observability", () => {
    const identifiersStart = panel.indexOf('<details className="run-identifiers">');

    expect(identifiersStart).toBeGreaterThan(-1);
    expect(panel).toContain("/api/harness/observability?run_id=");
    expect(panel).toContain("&trace_id=");
    expect(panel).toContain("activity.trace_id");
    expect(panel.slice(identifiersStart)).toContain("activity.run_id");
    expect(panel.slice(identifiersStart)).toContain("threadId");
    expect(panel).toContain('id="run-details-panel"');
    expect(panel).not.toContain("developerRows(threadId)");
  });

  it("removes streaming noise from the collapsed local activity list", () => {
    const item = (eventType: string, kind = "run", status = "succeeded", sequence = 1) => ({
      id: `${eventType}-${sequence}`,
      event_type: eventType,
      kind,
      status,
      title: eventType,
      summary: null,
      timestamp: "2026-07-16T00:00:00Z",
      sequence,
      metadata: {},
    }) as ActivityItem;

    const visible = notableActivityItems([
      item("message.delta", "analysis", "running", 1),
      item("tool.request", "tool", "running", 2),
      item("tool.result", "tool", "failed", 3),
      item("subagent.completed", "subagent", "completed", 4),
      item("run.succeeded", "result", "succeeded", 5),
    ]);

    expect(visible.map((entry) => entry.event_type)).toEqual([
      "tool.result",
      "subagent.completed",
      "run.succeeded",
    ]);
  });

  it("correlates exact tool input, return output, timing, model progress and artifacts", () => {
    const item = (
      eventType: string,
      sequence: number,
      metadata: Record<string, unknown> = {},
      summary: string | null = null,
      kind: ActivityItem["kind"] = "tool",
      status = "succeeded",
      timestamp = `2026-07-16T00:00:0${sequence}Z`,
    ) => ({
      id: `${eventType}-${sequence}`,
      event_type: eventType,
      kind,
      status,
      title: eventType,
      summary,
      timestamp,
      sequence,
      metadata,
    }) as ActivityItem;

    const trace = traceActivityEntries([
      item("message.start", 1, { message_id: "message-1" }, null, "analysis", "running"),
      item("message.delta", 2, { message_id: "message-1" }, "先核对输入。", "analysis"),
      item("message.delta", 3, { message_id: "message-1" }, "开始检测。", "analysis"),
      item("tool.request", 4, {
        tool_call_id: "tool-1",
        name: "Bash",
        arguments: { command: "python detect.py image.jpg", description: "检测图像" },
      }, null, "tool", "running"),
      item("tool.result", 5, {
        tool_call_id: "tool-1",
        result_summary: "返回 1 行 · 18 字符",
        result_preview: "detected: 2 objects",
      }),
      item("artifact.ready", 6, {
        artifact_id: "artifact-1",
        media_type: "application/json",
        size_bytes: 321,
      }, "outputs/detection.json", "artifact"),
    ]);

    expect(trace).toHaveLength(3);
    expect(trace[0]).toMatchObject({
      title: "模型进展说明",
      output: "先核对输入。开始检测。",
    });
    expect(trace[1]).toMatchObject({
      title: "检测图像",
      input: "python detect.py image.jpg",
      output: "detected: 2 objects",
      summary: "返回 1 行 · 18 字符",
      durationMs: 1000,
      status: "succeeded",
    });
    expect(trace[2].artifact).toEqual({
      id: "artifact-1",
      name: "outputs/detection.json",
      mediaType: "application/json",
      sizeBytes: 321,
    });
  });

  it("uses the same modal shell and width as the context panel", () => {
    expect(panel).toContain("createPortal");
    expect(panel).toContain('className="run-details-backdrop"');
    expect(panel).toContain('event.target === event.currentTarget');
    expect(styles).toMatch(/\.context-recovery-panel,\s*\.run-details-backdrop \.developer-drawer\s*\{[^}]*width:\s*min\(440px, 100vw\);/s);
  });

  it("uses semantic pulse colors for waiting and terminal states", () => {
    expect(styles).toMatch(
      /\.activity-pulse\.status-waiting\s*\{[^}]*background:\s*var\(--signal\);/s,
    );
    expect(styles).toMatch(
      /\.activity-pulse\.status-failed,[\s\S]*?\.activity-pulse\.status-timed_out\s*\{[^}]*background:\s*#a94442;/s,
    );
  });

  it("uses the shared dialog focus contract", () => {
    expect(panel).toContain("useDialogFocus");
    expect(panel).toContain('role="dialog"');
    expect(panel).toContain('aria-modal="true"');
    expect(panel).toContain("onEscape: () => onClose?.()");
    expect(panel).toContain("initialFocusRef: closeButtonRef");
  });
});
