import { describe, expect, it } from "vitest";
import type { ActivityItem, RunActivity } from "../src/lib/activity-schema";
import { assistantControlSummary } from "../src/lib/assistant-control-receipt";

const receipt = {
  kind: "work_notifications", objective_id: "objective-1", revision: 2,
  action: "query", summary: "结果通知已关闭。",
};
function item(event_type: string, sequence: number, metadata: Record<string, unknown> = {}): ActivityItem {
  return { id: String(sequence), event_type, sequence, metadata, kind: "result",
    status: "succeeded", title: "test", timestamp: "2026-11-02T00:00:00Z" };
}
function activity(items?: ActivityItem[]): RunActivity {
  return { run_id: "run-1", status: "succeeded", started_at: "2026-11-02T00:00:00Z", metrics: {},
    items: items ?? [
      { ...item("tool.request", 1, { name: "mcp__harness-builder__assistant_work_notifications" }), kind: "tool" },
      item("ui.assistant_control", 2, { assistant_control: receipt }),
      item("message.delta", 3),
    ] };
}

describe("confirmed assistant control receipts", () => {
  it("uses the same structured fact live and after history replay", () => {
    expect(assistantControlSummary(activity())).toBe(receipt.summary);
    expect(assistantControlSummary({ ...activity(), status: "running" })).toBe(receipt.summary);
    expect(assistantControlSummary(JSON.parse(JSON.stringify(activity())))).toBe(receipt.summary);
  });
  it.each(["failed", "rejected", "timed_out", "cancelled", "waiting_approval"])(
    "never collapses an unsuccessful or waiting run (%s)", (status) => {
      expect(assistantControlSummary({ ...activity(), status })).toBeNull();
    },
  );
  it("keeps the full answer when another tool starts after the receipt, even if it reports an error as text", () => {
    const next = item("tool.request", 4, { name: "mcp__harness-builder__assistant_work_notifications" });
    expect(assistantControlSummary(activity([...activity().items, next]))).toBeNull();
  });
  it("does not fold business work, artifacts or tool failures into a settings confirmation", () => {
    for (const extra of [
      { ...item("tool.result", 3), kind: "tool", status: "failed" },
      { ...item("artifact.ready", 4), kind: "artifact" },
      { ...item("tool.request", 1, { name: "mcp__harness-builder__assistant_create_task" }), kind: "tool" },
    ] as ActivityItem[]) {
      expect(assistantControlSummary(activity([...activity().items, extra]))).toBeNull();
    }
  });
  it("uses the last confirmed state instead of an earlier query", () => {
    const last = item("ui.assistant_control", 5, { assistant_control: {
      ...receipt, revision: 3, action: "update", summary: "结果通知开关已开启。",
    } });
    expect(assistantControlSummary(activity([...activity().items, last]))).toBe("结果通知开关已开启。");
  });
  it("ignores malformed, unscoped and tool-result lookalikes", () => {
    for (const rows of [
      [item("ui.assistant_control", 2, { assistant_control: receipt })],
      [activity().items[0], item("tool.result", 2, { assistant_control: receipt })],
      [activity().items[0], item("ui.assistant_control", 2, { assistant_control: { ...receipt, revision: 0 } })],
      [activity().items[0], item("ui.assistant_control", 2, { assistant_control: { ...receipt, extra: "x" } })],
    ]) expect(assistantControlSummary(activity(rows))).toBeNull();
  });
});
