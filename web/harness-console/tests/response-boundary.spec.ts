import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { activityStore } from "../src/lib/activity-store";
import { runActivitySchema } from "../src/lib/activity-schema";
import { syncProcessResponse } from "../src/lib/harness-agent";
import { liveResponseStore } from "../src/lib/live-response-store";
import { isResponseBoundary } from "../src/lib/process-boundary";

function publishLatest(eventType: string, runId = "run-1") {
  activityStore.publish(
    runActivitySchema.parse({
      run_id: runId,
      status: "running",
      started_at: "2026-09-18T00:00:00Z",
      items: [
        { id: "e1", event_type: "run.running", kind: "run", status: "succeeded", title: "运行中", summary: null, timestamp: "2026-09-18T00:00:01Z", sequence: 1, metadata: {} },
        { id: "e2", event_type: eventType, kind: "analysis", status: "succeeded", title: "事件", summary: null, timestamp: "2026-09-18T00:00:02Z", sequence: 2, metadata: {} },
      ],
      metrics: {},
    }),
    undefined,
  );
}

describe("response boundary", () => {
  it("matches the server's response projection", () => {
    // harness/agui/response.py cuts the final answer on these prefixes.
    for (const eventType of ["tool.request", "tool.result", "approval.requested", "subagent.started"]) {
      expect(isResponseBoundary(eventType)).toBe(true);
    }
  });

  it("does not treat thinking as the end of the answer", () => {
    // Providers interleave thinking with answer prose. Treating a thinking
    // delta as a boundary hid the streaming answer until the Run finished, so
    // the answer only appeared in one shot at the end.
    for (const eventType of ["reasoning.delta", "reasoning.summary.delta"]) {
      expect(isResponseBoundary(eventType)).toBe(false);
    }
    expect(isResponseBoundary("message.delta")).toBe(false);
    expect(isResponseBoundary("message.completed")).toBe(false);
    expect(isResponseBoundary("run.running")).toBe(false);
  });
});

describe("live answer keeps streaming through mid-answer thinking", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    liveResponseStore.clear();
    activityStore.clear();
  });
  afterEach(() => {
    liveResponseStore.clear();
    activityStore.clear();
    vi.useRealTimers();
  });

  it("does not hand a formed answer to the process log when thinking follows", () => {
    liveResponseStore.startRun("run-1");
    liveResponseStore.startMessage("assistant-run-1");
    const answer = "答案是 42。".repeat(30); // well past the 160-char candidate window
    liveResponseStore.append("assistant-run-1", answer);
    vi.advanceTimersByTime(300);
    expect(liveResponseStore.getSnapshot().visible).toBe(true);

    publishLatest("reasoning.delta");
    syncProcessResponse();

    // Before this guard the answer was hidden here and only reappeared when the
    // Run finished, which read as one-shot (non-streaming) output.
    expect(liveResponseStore.getSnapshot()).toMatchObject({ text: answer, visible: true });
  });

  it("does not hide short prose either: only actions end the answer", () => {
    // The server's projection keeps text across thinking blocks regardless of
    // length, so the client must too, or reloaded answers differ from streamed
    // ones and short replies vanish mid-run.
    liveResponseStore.startRun("run-1");
    liveResponseStore.startMessage("assistant-run-1");
    liveResponseStore.append("assistant-run-1", "先查一下资料。");
    vi.advanceTimersByTime(300);
    expect(liveResponseStore.getSnapshot().visible).toBe(true);

    publishLatest("reasoning.delta");
    syncProcessResponse();

    expect(liveResponseStore.getSnapshot()).toMatchObject({
      text: "先查一下资料。",
      visible: true,
    });
  });

  it("hands prose to the process log when a tool follows, however long it is", () => {
    liveResponseStore.startRun("run-1");
    liveResponseStore.startMessage("assistant-run-1");
    liveResponseStore.append("assistant-run-1", "先核验来源。".repeat(40));
    vi.advanceTimersByTime(300);

    publishLatest("tool.request");
    syncProcessResponse();

    // The server's projection cuts the answer on tool events, so the client
    // must agree or the reloaded answer would differ from the streamed one.
    expect(liveResponseStore.getSnapshot().visible).toBe(false);
  });
});
