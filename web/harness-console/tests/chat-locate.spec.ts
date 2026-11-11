// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { flashMessage, locateInChat, registerHistoryLocator } from "../src/lib/chat-locate";

afterEach(() => {
  registerHistoryLocator(null);
  document.body.replaceChildren();
  vi.useRealTimers();
});

function addMessage(id: string) {
  const node = document.createElement("div");
  node.setAttribute("data-message-id", id);
  node.scrollIntoView = vi.fn();
  document.body.append(node);
  return node;
}

it("flashes a message that is already mounted", async () => {
  const node = addMessage("user-run_1");
  expect(flashMessage("user-run_1")).toBe(true);
  expect(node.classList.contains("assistant-history-located")).toBe(true);
  expect(node.scrollIntoView).toHaveBeenCalled();
  await expect(locateInChat("user-run_1")).resolves.toBe("located");
});

it("pages earlier history in before locating an old message", async () => {
  const loadEarlier = vi.fn(async (_pages = 1) => { addMessage("assistant-run_old"); return true; });
  let hasMore = true;
  registerHistoryLocator({
    threadId: "thread_1",
    hasMore: () => hasMore,
    loadEarlier: async (pages = 1) => {
      await loadEarlier(pages);
      hasMore = false;
      return true;
    },
  });
  await expect(locateInChat("assistant-run_old", "thread_1")).resolves.toBe("located");
  expect(loadEarlier).toHaveBeenCalledTimes(1);
  expect(loadEarlier).toHaveBeenCalledWith(3);
});

it("keeps paging past the old five-page ceiling", async () => {
  let remaining = 7;
  const loadEarlier = vi.fn(async () => {
    remaining -= 1;
    if (remaining === 0) addMessage("assistant-run_oldest");
    return true;
  });
  registerHistoryLocator({
    threadId: "thread_1",
    hasMore: () => remaining > 0,
    loadEarlier,
  });
  // 7 batches × 3 pages = 21 pages of 30 runs, beyond the old fixed 5.
  await expect(locateInChat("assistant-run_oldest", "thread_1")).resolves.toBe("located");
  expect(loadEarlier).toHaveBeenCalledTimes(7);
}, 15_000);

it("stops when the thread has no earlier pages or belongs to another thread", async () => {
  const loadEarlier = vi.fn(async () => false);
  registerHistoryLocator({ threadId: "thread_1", hasMore: () => false, loadEarlier });
  await expect(locateInChat("missing", "thread_1")).resolves.toBe("missing");
  registerHistoryLocator({ threadId: "thread_1", hasMore: () => true, loadEarlier });
  await expect(locateInChat("missing", "thread_2")).resolves.toBe("missing");
  expect(loadEarlier).not.toHaveBeenCalled();
});

it("does not match a message outside the registered conversation frame", async () => {
  const frame = document.createElement("div");
  const viewport = document.createElement("div");
  viewport.className = "aui-thread-viewport";
  frame.append(viewport);
  document.body.append(frame);
  addMessage("assistant-other");
  registerHistoryLocator({
    threadId: "thread_1",
    hasMore: () => false,
    loadEarlier: async () => false,
    frame: () => frame,
  });
  await expect(locateInChat("assistant-other", "thread_1")).resolves.toBe("missing");
});

it("does not fall back to the document when the registered frame is gone", async () => {
  addMessage("assistant-other");
  registerHistoryLocator({
    threadId: "thread_1",
    hasMore: () => false,
    loadEarlier: async () => false,
    // The conversation frame unmounted; the message node elsewhere on the
    // page must not be treated as a match.
    frame: () => null,
  });
  await expect(locateInChat("assistant-other", "thread_1")).resolves.toBe("retry");
});

it("waits for an in-flight page instead of reporting a missing message", async () => {
  let finish!: () => void;
  const inFlight = new Promise<void>(resolve => { finish = resolve; });
  let busy = true;
  const loadEarlier = vi.fn(async () => {
    if (busy) return "busy" as const;
    addMessage("assistant-run_old");
    return "loaded" as const;
  });
  registerHistoryLocator({
    threadId: "thread_1",
    hasMore: () => true,
    loadEarlier,
    waitForLoading: async () => { busy = false; await inFlight; },
  });
  const located = locateInChat("assistant-run_old", "thread_1");
  setTimeout(() => finish(), 0);
  await expect(located).resolves.toBe("located");
  expect(loadEarlier).toHaveBeenCalledTimes(2);
});

it("reports retry, not missing, when the page request stays busy", async () => {
  const loadEarlier = vi.fn(async () => "busy" as const);
  registerHistoryLocator({
    threadId: "thread_1",
    hasMore: () => true,
    loadEarlier,
    waitForLoading: async () => undefined,
  });
  await expect(locateInChat("assistant-run_old", "thread_1")).resolves.toBe("retry");
  expect(loadEarlier).toHaveBeenCalledTimes(2);
});

it("reports retry, not missing, when an earlier page fails to load", async () => {
  const loadEarlier = vi.fn(async () => "failed" as const);
  registerHistoryLocator({ threadId: "thread_1", hasMore: () => true, loadEarlier });
  await expect(locateInChat("assistant-run_old", "thread_1")).resolves.toBe("retry");
});

it("stops paging the old thread when the locator is replaced mid-locate", async () => {
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const oldLoad = vi.fn(async () => { await gate; return "loaded" as const; });
  registerHistoryLocator({ threadId: "thread_1", hasMore: () => true, loadEarlier: oldLoad });
  const pending = locateInChat("assistant-run_old", "thread_1");
  const newLoad = vi.fn(async () => "loaded" as const);
  registerHistoryLocator({ threadId: "thread_1", hasMore: () => true, loadEarlier: newLoad });
  release();
  await expect(pending).resolves.toBe("retry");
  expect(newLoad).not.toHaveBeenCalled();
});
