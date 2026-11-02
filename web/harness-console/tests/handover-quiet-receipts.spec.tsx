import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { HandoverInlineRows } from "../src/components/handover-row";
import { emitHandover } from "./helpers/handover";
import { handoverStore } from "../src/lib/handover-store";

describe("quiet handover receipts", () => {
  it("collapses settled receipts while keeping pending and failed actions open", () => {
    for (const status of ["applied", "pending_input", "failed"]) {
      emitHandover({ specialist_id: status, specialist_name: status, status });
    }
    const html = renderToStaticMarkup(<HandoverInlineRows messageId="assistant-run-ui-test" />);
    expect(html).not.toContain("<details");
    expect(html).not.toContain("查看回执");
    expect(html).not.toContain("已登记转交");
    expect(html).toContain("已安排");
    expect(html).toContain("待补充");
    expect(html).toContain('role="alert"');
  });

  it("a delayed history snapshot cannot replace a live receipt", async () => {
    const { handoverStore: isolated } = await import("../src/lib/handover-store");
    vi.doMock("../src/lib/assistant-client", () => ({
      assistantRequest: async (path: string) => path === "handovers"
        ? [{ handoverId: "handover-live", messageId: "delegate-specialist:run-ui-test:live", specialistId: "live", status: "sent", createdAt: "2026-01-01T00:00:00Z" }]
        : [{ specialistId: "live", name: "old" }],
    }));
    isolated.ingest({ handover_id: "handover-live", run_id: "run-ui-test", specialist_id: "live", specialist_name: "new", status: "applied" });
    await isolated.seed();
    expect(isolated.snapshot().find((n) => n.specialistId === "live")?.status).toBe("applied");
    vi.doUnmock("../src/lib/assistant-client");
  });
});
