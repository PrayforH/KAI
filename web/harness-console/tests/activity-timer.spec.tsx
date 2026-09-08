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
