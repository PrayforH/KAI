import { describe, expect, it, vi } from "vitest";
import { activityStore } from "../src/lib/activity-store";
import { runActivitySchema } from "../src/lib/activity-schema";

describe("activity store", () => {
  it("shares the renderer's replayable activity with the run inspector", () => {
    const listener = vi.fn();
    const unsubscribe = activityStore.subscribe(listener);
    const activity = runActivitySchema.parse({
      run_id: "run-store",
      status: "running",
      started_at: "2026-07-13T00:00:00Z",
      items: [],
      metrics: {},
    });

    activityStore.publish(activity);

    expect(activityStore.getSnapshot()).toBe(activity);
    expect(listener).toHaveBeenCalledOnce();
    unsubscribe();
  });

  it("applies Harness activity deltas without exposing the protocol as chat text", () => {
    activityStore.publish(
      runActivitySchema.parse({
        run_id: "run-delta",
        status: "queued",
        started_at: "2026-07-13T00:00:00Z",
        items: [],
        metrics: {},
      }),
    );

    activityStore.patch([
      {
        op: "add",
        path: "/items/-",
        value: {
          id: "event-1",
          event_type: "tool.request",
          kind: "tool",
          status: "running",
          title: "调用 Read",
          timestamp: "2026-07-13T00:00:01Z",
          sequence: 1,
          metadata: { name: "Read" },
        },
      },
      { op: "replace", path: "/status", value: "running" },
      { op: "add", path: "/metrics/turns", value: 1 },
    ]);

    expect(activityStore.getSnapshot()).toMatchObject({
      status: "running",
      metrics: { turns: 1 },
      items: [{ title: "调用 Read" }],
    });
  });

  it("clears stale run activity when the user starts a new thread", () => {
    const listener = vi.fn();
    const unsubscribe = activityStore.subscribe(listener);
    activityStore.publish(
      runActivitySchema.parse({
        run_id: "run-old-thread",
        status: "running",
        started_at: "2026-07-13T00:00:00Z",
        items: [],
        metrics: {},
      }),
    );

    activityStore.clear();

    expect(activityStore.getSnapshot()).toBeUndefined();
    expect(listener).toHaveBeenCalledTimes(2);
    unsubscribe();
  });

  it("projects one monotonic run view from snapshots and deltas", () => {
    expect("getViewSnapshot" in activityStore).toBe(true);
    const store = activityStore as typeof activityStore & {
      getViewSnapshot: () => { phase: string } | undefined;
    };
    activityStore.publish(
      runActivitySchema.parse({
        run_id: "run-view-store",
        status: "running",
        started_at: "2026-07-13T00:00:00Z",
        items: [
          {
            id: "run-start",
            event_type: "run.running",
            kind: "run",
            status: "running",
            title: "Agent 开始执行",
            timestamp: "2026-07-13T00:00:01Z",
            sequence: 1,
            metadata: {},
          },
        ],
        metrics: {},
      }),
    );
    activityStore.patch([
      {
        op: "add",
        path: "/items/-",
        value: {
          id: "runtime-result",
          event_type: "runtime.result",
          kind: "result",
          status: "succeeded",
          title: "模型执行完成",
          timestamp: "2026-07-13T00:00:02Z",
          sequence: 2,
          metadata: {},
        },
      },
      { op: "replace", path: "/status", value: "succeeded" },
    ]);

    expect(store.getViewSnapshot()?.phase).toBe("running");

    activityStore.patch([
      {
        op: "add",
        path: "/items/-",
        value: {
          id: "run-complete",
          event_type: "run.succeeded",
          kind: "run",
          status: "succeeded",
          title: "运行完成",
          timestamp: "2026-07-13T00:00:03Z",
          sequence: 3,
          metadata: {},
        },
      },
    ]);
    activityStore.patch([{ op: "replace", path: "/status", value: "running" }]);

    expect(store.getViewSnapshot()?.phase).toBe("completed");
  });
});

describe("durable history publish", () => {
  it("merges live fragments by default and replaces the same run when asked", () => {
    activityStore.clear();
    const fragment = (id: string, sequence: number, item_id: string, summary: string) => ({
      id,
      event_type: "reasoning.delta",
      kind: "analysis" as const,
      status: "completed",
      title: "思考",
      summary,
      timestamp: `2026-07-13T00:00:0${sequence}Z`,
      sequence,
      metadata: { item_id },
    });
    const live = runActivitySchema.parse({
      run_id: "run-replace",
      status: "succeeded",
      started_at: "2026-07-13T00:00:00Z",
      items: [
        fragment("f1", 1, "block", "第一段"),
        fragment("f2", 2, "block", "第二段"),
      ],
      metrics: {},
    });
    // The durable projection folded the block into one item kept at f1's id.
    const durable = runActivitySchema.parse({
      run_id: "run-replace",
      status: "succeeded",
      started_at: "2026-07-13T00:00:00Z",
      items: [fragment("f1", 1, "block", "第一段第二段")],
      metrics: {},
    });

    activityStore.publish(live);
    activityStore.publish(durable);
    const merged = activityStore.getViewSnapshot();
    expect(merged?.items.map((item) => item.summary)).toEqual([
      "第一段第二段",
      "第二段",
    ]);

    activityStore.publish(durable, undefined, { replaceRun: true });
    const replaced = activityStore.getViewSnapshot();
    expect(replaced?.items.map((item) => item.summary)).toEqual(["第一段第二段"]);
  });
});
