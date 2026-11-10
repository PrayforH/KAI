// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { flashMessage, locateInChat, registerHistoryLocator } from "../src/lib/chat-locate";

afterEach(() => {
  registerHistoryLocator(null);
  document.querySelectorAll("[data-message-id]").forEach(node => node.remove());
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
  await expect(locateInChat("user-run_1")).resolves.toBe(true);
});

it("pages earlier history in before locating an old message", async () => {
  const loadEarlier = vi.fn(async () => { addMessage("assistant-run_old"); });
  let hasMore = true;
  registerHistoryLocator({
    threadId: "thread_1",
    hasMore: () => hasMore,
    loadEarlier: async () => { await loadEarlier(); hasMore = false; },
  });
  await expect(locateInChat("assistant-run_old", "thread_1")).resolves.toBe(true);
  expect(loadEarlier).toHaveBeenCalledTimes(1);
});

it("stops when the thread has no earlier pages or belongs to another thread", async () => {
  const loadEarlier = vi.fn(async () => undefined);
  registerHistoryLocator({ threadId: "thread_1", hasMore: () => false, loadEarlier });
  await expect(locateInChat("missing", "thread_1")).resolves.toBe(false);
  registerHistoryLocator({ threadId: "thread_1", hasMore: () => true, loadEarlier });
  await expect(locateInChat("missing", "thread_2")).resolves.toBe(false);
  expect(loadEarlier).not.toHaveBeenCalled();
});
