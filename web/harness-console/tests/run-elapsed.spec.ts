// @vitest-environment jsdom
import { beforeEach, expect, it, vi } from "vitest";
import type { RunViewModel } from "../src/lib/run-view-model";

function view(runId = "a", elapsedMs = 0, phase: RunViewModel["phase"] = "running"): RunViewModel {
  return { runId, elapsedMs, phase, startedAt: "2026-09-07T00:00:00Z",
    updatedAt: "2026-09-07T00:00:00Z", summary: "", items: [], tasks: [], tools: [], taskCount: 0, toolCount: 0 };
}

beforeEach(() => { vi.resetModules(); sessionStorage.clear(); });

it("starts a new run at zero with a browser clock 40 seconds ahead or behind", async () => {
  const { elapsedAnchorFor, activeElapsedMs } = await import("../src/lib/run-elapsed");
  for (const skew of [-40_000, 40_000]) {
    const run = view(String(skew));
    const now = Date.parse(run.startedAt) + skew;
    const anchor = elapsedAnchorFor(run, now);
    expect(activeElapsedMs(run, now, anchor)).toBe(0);
    expect(activeElapsedMs(run, now + 1_000, anchor)).toBe(1_000);
  }
});

it("isolates new turns and continues the same run after switching away and back", async () => {
  const { elapsedAnchorFor, activeElapsedMs } = await import("../src/lib/run-elapsed");
  elapsedAnchorFor(view(), 1_000);
  const next = view("b");
  expect(activeElapsedMs(next, 41_000, elapsedAnchorFor(next, 41_000))).toBe(0);
  expect(activeElapsedMs(view(), 46_000, elapsedAnchorFor(view(), 46_000))).toBe(45_000);
});

it("restores the local anchor after a reload without adding server clock skew", async () => {
  const first = await import("../src/lib/run-elapsed");
  first.elapsedAnchorFor(view(), 1_000);
  vi.resetModules();
  const restored = await import("../src/lib/run-elapsed");
  expect(restored.activeElapsedMs(view(), 41_000, restored.elapsedAnchorFor(view(), 41_000))).toBe(40_000);
});

it("rebases on newer server duration and keeps ticking without waiting to catch up", async () => {
  const { elapsedAnchorFor, activeElapsedMs } = await import("../src/lib/run-elapsed");
  elapsedAnchorFor(view(), 1_000);
  const updated = view("a", 20_000);
  const anchor = elapsedAnchorFor(updated, 2_000);
  expect(activeElapsedMs(updated, 3_000, anchor)).toBe(21_000);
  // A delayed snapshot cannot move the displayed clock backwards.
  expect(activeElapsedMs(view(), 4_000, elapsedAnchorFor(view(), 4_000))).toBe(22_000);
});

it.each(["completed", "failed", "cancelled", "rejected"] as const)("stops immediately on %s", async (phase) => {
  const { elapsedAnchorFor, activeElapsedMs } = await import("../src/lib/run-elapsed");
  const anchor = elapsedAnchorFor(view(), 1_000);
  expect(activeElapsedMs(view("a", 10_000, phase), 91_000, anchor)).toBe(10_000);
});

it("keeps working when session storage is unavailable", async () => {
  const get = vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
  const set = vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
  try {
    const { elapsedAnchorFor, activeElapsedMs } = await import("../src/lib/run-elapsed");
    elapsedAnchorFor(view(), 1_000);
    expect(activeElapsedMs(view(), 3_000, elapsedAnchorFor(view(), 3_000))).toBe(2_000);
  } finally { get.mockRestore(); set.mockRestore(); }
});
