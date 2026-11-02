import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ActivitySummary } from "../src/components/activity-summary";

describe("unconfirmed steering history", () => {
  it("replays the original input and uncertainty after a completed run, without claiming acceptance", () => {
    const html = renderToStaticMarkup(<ActivitySummary activity={{
      run_id: "recovered-run", status: "succeeded", started_at: "2026-11-02T01:00:00Z", metrics: {}, items: [
        { id: "requested", event_type: "run.steer.requested", kind: "analysis", status: "waiting",
          title: "等待接收补充说明", summary: "保留原目录", sequence: 1,
          timestamp: "2026-11-02T01:00:00Z", metadata: { request_id: "input-1" } },
        { id: "unknown", event_type: "run.steer.unknown", kind: "analysis", status: "waiting",
          title: "补充接收状态待核对", summary: "保留原目录", sequence: 2,
          timestamp: "2026-11-02T01:00:01Z", metadata: { request_id: "input-1" } },
      ],
    }} />);
    expect(html).toContain('data-steering-status="unknown"');
    expect(html).toContain("保留原目录");
    expect(html).toContain("接收状态待核对，请查看回复后决定是否重发。");
    expect(html).not.toContain('data-steering-status="accepted"');
    expect(html).not.toContain("等待接收…");
  });
});
