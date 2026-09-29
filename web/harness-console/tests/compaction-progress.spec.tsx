import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CompactionProgressIndicator } from "../src/components/compaction-progress";
import { compactionProgress } from "../src/lib/compaction-progress";
import { reduceRunViewModel } from "../src/lib/run-view-model";

function view(types: string[], status = "running") {
  return reduceRunViewModel(undefined, {
    run_id: "run", status, started_at: "2026-09-28T00:00:00Z", metrics: {},
    items: types.map((event_type, sequence) => ({
      id: String(sequence), event_type, sequence, kind: "analysis" as const,
      title: event_type, status: "running", timestamp: "2026-09-28T00:00:00Z", metadata: {},
    })),
  });
}

describe("real compaction lifecycle", () => {
  it("does not infer compaction from normal work", () => {
    expect(compactionProgress(view(["run.running", "usage.updated"]))).toBeNull();
  });
  it("keeps the spinner until an actual compaction terminal, independent of unrelated events", () => {
    const state = view(["context.compaction.started", "usage.updated"]);
    expect(compactionProgress(state)?.state).toBe("running");
    const html = renderToStaticMarkup(<CompactionProgressIndicator view={state} />);
    expect(html).toContain("正在自动压缩上下文");
    expect(html).toContain('role="status"');
    expect(html).toContain("is-running");
  });
  it("shows completion and allows the next compaction to start", () => {
    const done = view(["context.compaction.started", "context.compacted"]);
    expect(compactionProgress(done)?.state).toBe("completed");
    expect(renderToStaticMarkup(<CompactionProgressIndicator view={done} />)).toContain("上下文已压缩");
    expect(compactionProgress(view(["context.compacted", "context.compaction.started"]))?.state).toBe("running");
  });
  it.each(["run.failed", "run.cancelled", "run.timed_out", "run.succeeded"])("stops animation on %s even if the provider omitted a terminal", (terminal) => {
    const state = view(["context.compaction.started", terminal]);
    expect(compactionProgress(state)?.state).toBe("interrupted");
    expect(renderToStaticMarkup(<CompactionProgressIndicator view={state} />)).not.toContain("is-running");
  });
  it("does not replay success when the durable checkpoint arrives after request compaction", () => {
    const live = compactionProgress(view(["context.compaction.started", "context.compaction.completed"]));
    const saved = compactionProgress(view(["context.compaction.started", "context.compaction.completed", "context.compacted"]));
    expect(saved).toEqual(live);
  });
  it("preserves a real failure and does not replay historical success notifications", () => {
    expect(compactionProgress(view(["context.compaction.started", "context.compaction.failed"]))?.state).toBe("failed");
    const history = view(["context.compacted", "run.succeeded"]);
    expect(renderToStaticMarkup(<CompactionProgressIndicator view={history} />)).toBe("");
  });
});
