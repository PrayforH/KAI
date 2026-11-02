import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
let store: typeof import("../src/lib/handover-store");
const receipt = (id: string, run: string, extra: Record<string, unknown> = {}) => ({
  handover_id: id, run_id: run, specialist_id: "same-bot", specialist_name: "协同助理", status: "applied",
  created_at: "2026-11-02T01:00:00Z", updated_at: "2026-11-02T01:01:00Z", ...extra,
});
beforeEach(async () => { vi.resetModules(); store = await import("../src/lib/handover-store"); });
afterEach(() => { vi.restoreAllMocks(); vi.doUnmock("../src/lib/assistant-client"); });
describe("handover source ownership", () => {
  it("keeps different requests to the same bot on their own messages instead of the newest answer", () => {
    store.handoverStore.ingest(receipt("one", "run-one"));
    store.handoverStore.ingest(receipt("two", "run-two", { created_at: "2026-11-02T02:00:00Z" }));
    const all = store.handoverStore.snapshot();
    expect(all).toHaveLength(2);
    expect(store.handoversForMessage(all, "assistant-run-one").map(x => x.handoverId)).toEqual(["one"]);
    expect(store.handoversForMessage(all, "assistant-run-two").map(x => x.handoverId)).toEqual(["two"]);
    expect(store.handoversForMessage(all, "assistant-unrelated")).toEqual([]);
    expect(store.isLatestHandover(all.find(x => x.handoverId === "one")!, all)).toBe(false);
    expect(store.isLatestHandover(all.find(x => x.handoverId === "two")!, all)).toBe(true);
  });
  it("recovers source runs from persisted delegation keys without retaining a global latest-three view", async () => {
    vi.doMock("../src/lib/assistant-client", () => ({ assistantRequest: async (path: string) => path === "handovers"
      ? Array.from({ length: 5 }, (_, n) => ({ handoverId: `h${n}`, messageId: `delegate-specialist:run${n}:联系人:含冒号`, specialistId: `s${n}`, status: "sent", createdAt: "2026-11-02T01:00:00Z" }))
      : [] }));
    await store.handoverStore.seed();
    expect(store.handoverStore.snapshot()).toHaveLength(5);
    expect(store.handoversForMessage(store.handoverStore.snapshot(), "assistant-run0")[0]?.handoverId).toBe("h0");
    expect(store.sourceRunId("a message without source ownership")).toBeNull();
  });
  it("ignores replayed stale states and keeps newer request ownership even when an older receipt updates", () => {
    store.handoverStore.ingest(receipt("one", "run-one", { updated_at: "2026-11-02T03:00:00Z" }));
    store.handoverStore.ingest(receipt("one", "run-one", { status: "pending_input" }));
    store.handoverStore.ingest(receipt("two", "run-two", { created_at: "2026-11-02T02:00:00Z" }));
    const all = store.handoverStore.snapshot();
    expect(all.find(x => x.handoverId === "one")?.status).toBe("applied");
    expect(store.isLatestHandover(all.find(x => x.handoverId === "one")!, all)).toBe(false);
  });
  it("attaches an optimistic message only to its active run, with no unrelated last-message fallback", () => {
    store.handoverStore.ingest(receipt("one", "run-one"));
    const all = store.handoverStore.snapshot();
    expect(store.handoversForMessage(all, "__optimistic__message", "run-one")).toHaveLength(1);
    expect(store.handoversForMessage(all, "__optimistic__message", "new-run")).toEqual([]);
    expect(store.handoversForMessage(all, "assistant-other", "run-one")).toEqual([]);
  });
  it("settles only the confirmed handover even if the browser clock is behind server timestamps", () => {
    store.handoverStore.ingest(receipt("one", "run-one", { status: "pending_input" }));
    store.handoverStore.ingest(receipt("two", "run-two", { status: "pending_input" }));
    vi.spyOn(Date, "now").mockReturnValue(0);
    store.handoverStore.markConfigured("one");
    expect(store.handoverStore.snapshot().find(x => x.handoverId === "one")?.status).toBe("applied");
    expect(store.handoverStore.snapshot().find(x => x.handoverId === "two")?.status).toBe("pending_input");
  });
  it("does not fabricate a source or a successful receipt for unscoped and unknown events", () => {
    store.handoverStore.ingest({ specialist_id: "bot", specialist_name: "旧数据" });
    expect(store.handoverStore.snapshot()).toEqual([]);
    expect(store.statusLead("unknown")).toContain("待核实");
  });
});
