import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";
import { HandoverInlineRows } from "../src/components/handover-row";
import { handoverStore } from "../src/lib/handover-store";

describe("quiet handover receipts", () => {
  it("collapses settled receipts while keeping pending and failed actions open", () => {
    for (const status of ["applied", "pending_input", "failed"]) {
      handoverStore.ingest({ specialist_id: status, specialist_name: status, status });
    }
    const html = renderToStaticMarkup(<HandoverInlineRows />);
    const details = [...html.matchAll(/<details[^>]*>[\s\S]*?<\/summary>/g)].map((m) => m[0]);
    expect(details).toHaveLength(3);
    expect(details.find((d) => d.includes("bot=applied"))).not.toContain(" open=");
    expect(details.find((d) => d.includes("bot=pending_input"))).toContain(" open=");
    expect(details.find((d) => d.includes("bot=failed"))).toContain(" open=");
    expect(html).toContain("查看回执");
  });

  it("a delayed history snapshot cannot replace a live receipt", async () => {
    const { handoverStore: isolated } = await import("../src/lib/handover-store");
    vi.doMock("../src/lib/assistant-client", () => ({
      assistantRequest: async (path: string) => path === "handovers"
        ? [{ specialistId: "live", status: "sent", createdAt: "2026-01-01T00:00:00Z" }]
        : [{ specialistId: "live", name: "old" }],
    }));
    isolated.ingest({ specialist_id: "live", specialist_name: "new", status: "applied" });
    await isolated.seed();
    expect(isolated.snapshot().find((n) => n.specialistId === "live")?.status).toBe("applied");
    vi.doUnmock("../src/lib/assistant-client");
  });
});
