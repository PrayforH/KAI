// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { ActivitySummary } from "../src/components/activity-summary";
import { runActivitySchema } from "../src/lib/activity-schema";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

it("renders a fresh timer, preserves it across remounts, and resets for the next run", async () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-09-07T00:00:40Z"));
  const activity = runActivitySchema.parse({ run_id: "timer-dom-a", status: "running",
    started_at: "2026-09-07T00:00:00Z", items: [] });
  const container = document.createElement("div"); document.body.appendChild(container);
  const root = createRoot(container);
  const duration = () => container.querySelector(".execution-duration")?.textContent;
  try {
    await act(async () => root.render(<ActivitySummary activity={activity} />));
    expect(duration()).toContain("已持续 0s");
    await act(async () => vi.advanceTimersByTime(3_000));
    expect(duration()).toContain("已持续 3s");
    await act(async () => root.render(null));
    await act(async () => vi.advanceTimersByTime(2_000));
    await act(async () => root.render(<ActivitySummary activity={activity} />));
    expect(duration()).toContain("已持续 5s");
    await act(async () => root.render(<ActivitySummary activity={{ ...activity, run_id: "timer-dom-b" }} />));
    expect(duration()).toContain("已持续 0s");
    await act(async () => vi.advanceTimersByTime(1_000));
    expect(duration()).toContain("已持续 1s");
  } finally { await act(async () => root.unmount()); container.remove(); vi.useRealTimers(); }
});

it("keeps a user's expanded process open through completion and history remount", async () => {
  const runId = "manual-disclosure-regression";
  const item = { id: "tool-one", event_type: "tool.request", kind: "tool", status: "running", title: "读取文件", sequence: 1, timestamp: "2026-09-07T00:00:01Z", metadata: { tool_call_id: "read", name: "Read" } };
  const activity = runActivitySchema.parse({ run_id: runId, status: "running", started_at: "2026-09-07T00:00:00Z", items: [item] });
  const host = document.createElement("div"); document.body.appendChild(host); const root = createRoot(host);
  try {
    await act(async () => root.render(<ActivitySummary activity={activity} />));
    const toggle = () => (host.querySelector('.execution-disclosure') as HTMLButtonElement).click();
    await act(async () => toggle()); // User closes, then explicitly opens it.
    await act(async () => toggle());
    const completed = runActivitySchema.parse({ ...activity, status: "succeeded", items: [...activity.items, { ...item, id: "done", event_type: "run.succeeded", status: "completed", sequence: 2 }] });
    await act(async () => root.render(<ActivitySummary activity={completed} />));
    expect(host.querySelector('.execution-disclosure')?.getAttribute('aria-expanded')).toBe('true');
    await act(async () => root.render(null));
    await act(async () => root.render(<ActivitySummary activity={completed} />));
    expect(host.querySelector('.execution-disclosure')?.getAttribute('aria-expanded')).toBe('true');
  } finally { await act(async () => root.unmount()); host.remove(); window.localStorage.removeItem?.(`agent-studio:run-disclosure:v1:${runId}`); }
});
