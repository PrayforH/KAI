// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it } from "vitest";
import { ActivitySummary } from "../src/components/activity-summary";
import { runActivitySchema } from "../src/lib/activity-schema";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

let container: HTMLDivElement | null = null;
let root: ReturnType<typeof createRoot> | null = null;

function renderQueueActivity(reasonCode: string | null) {
  const activity = runActivitySchema.parse({
    run_id: "queue-hint-run",
    status: "queued",
    started_at: "2026-09-07T00:00:00Z",
    items: [
      {
        id: "queued",
        event_type: "run.queued",
        kind: "run",
        status: "queued",
        title: "前序任务仍在执行",
        summary: "前序任务仍在执行，已进入队列",
        sequence: 1,
        timestamp: "2026-09-07T00:00:00Z",
        metadata: reasonCode ? { reason_code: reasonCode } : {},
      },
    ],
  });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  return { activity, container };
}

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  container?.remove();
  root = null;
  container = null;
});

it("shows a single inline hint while a predecessor waits for approval", async () => {
  const { activity, container: host } = renderQueueActivity("predecessor_waiting_approval");
  await act(async () => root!.render(<ActivitySummary activity={activity} />));
  const hint = host.querySelector(".execution-queue-hint");
  expect(hint?.textContent).toContain("前序任务等待审批");
  expect(host.querySelector(".composer-queue-notice")).toBeNull();
});

it("stays quiet for other queue reasons", async () => {
  const { activity, container: host } = renderQueueActivity("predecessor_active");
  await act(async () => root!.render(<ActivitySummary activity={activity} />));
  expect(host.querySelector(".execution-queue-hint")).toBeNull();
});
