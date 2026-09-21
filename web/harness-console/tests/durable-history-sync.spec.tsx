// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { DurableHistorySync } from "../src/components/durable-history-sync";
import { liveResponseStore } from "../src/lib/live-response-store";

const state = vi.hoisted(() => ({ running: false, import: vi.fn() }));
const thread = { getState: () => ({ isRunning: state.running }), import: state.import };
vi.mock("@assistant-ui/react", () => ({
  useThreadRuntime: () => thread,
  useAuiState: () => state.running,
}));
vi.mock("../src/lib/activity-store", () => ({ useRunViewModel: () => ({ phase: "completed" }) }));
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let host: HTMLDivElement;
let root: Root;
const repository = { messages: [], headId: null };
const loadSnapshot = vi.fn(async (onLoaded?: (value: typeof repository) => void) => {
  onLoaded?.(repository);
  return repository;
});
const history = { loadSnapshot } as unknown as React.ComponentProps<typeof DurableHistorySync>["history"];
async function render(revision: number) {
  await act(async () => { root.render(<DurableHistorySync threadId="sync-thread" history={history} revision={revision} />); });
  await act(async () => { await vi.advanceTimersByTimeAsync(20); });
}
beforeEach(() => {
  vi.useFakeTimers(); state.running = false; state.import.mockClear(); loadSnapshot.mockClear();
  host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host);
  liveResponseStore.clear();
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); liveResponseStore.clear(); vi.useRealTimers(); });

it("retries completion history when the runtime settles after the completion callback", async () => {
  state.running = true;
  await render(0);
  await render(1);
  expect(loadSnapshot).not.toHaveBeenCalled();
  state.running = false;
  await render(1);
  expect(state.import).toHaveBeenCalledWith(repository);
});

it("hands rendering back to durable history instead of leaving a hidden stream in charge", async () => {
  liveResponseStore.startRun("run", "sync-thread");
  liveResponseStore.startMessage("assistant-run", "sync-thread");
  liveResponseStore.append("assistant-run", "Searching", "sync-thread");
  liveResponseStore.hideForTool("sync-thread");
  liveResponseStore.completeRun("sync-thread");
  await render(1);
  expect(state.import).toHaveBeenCalledWith(repository);
  expect(liveResponseStore.getSnapshot().status).toBe("idle");
});

it("does not import or clear a new run when an older history fetch resolves", async () => {
  let deliver: ((value: typeof repository) => void) | undefined;
  loadSnapshot.mockImplementationOnce(async (callback) => { deliver = callback; return repository; });
  await render(0);
  state.running = true;
  liveResponseStore.startRun("new-run", "sync-thread");
  await render(0);
  await act(async () => deliver?.(repository));
  expect(state.import).not.toHaveBeenCalled();
  expect(liveResponseStore.getSnapshot().runId).toBe("new-run");
});
