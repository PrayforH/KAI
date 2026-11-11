// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { flashMessage, locateInChat, registerHistoryLocator } from "../src/lib/chat-locate";
import {
  beginLocate, getLocatePhase, resetLocatePhases, settleLocate, subscribeLocatePhase,
} from "../src/lib/locate-signal";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

afterEach(() => {
  registerHistoryLocator(null);
  resetLocatePhases();
  document.body.replaceChildren();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

function addMessage(id: string, top = 0, parent: HTMLElement = document.body): HTMLElement {
  const node = document.createElement("div");
  node.setAttribute("data-message-id", id);
  node.getBoundingClientRect = () => ({ top } as DOMRect);
  node.scrollIntoView = vi.fn();
  parent.append(node);
  return node;
}

/** A scroll viewport with a controllable distance and a spy for WAAPI fades. */
function addViewport(clientHeight = 600) {
  const viewport = document.createElement("div");
  viewport.className = "aui-thread-viewport";
  viewport.getBoundingClientRect = () => ({ top: 0 } as DOMRect);
  Object.defineProperty(viewport, "clientHeight", { configurable: true, value: clientHeight });
  const animate = vi.fn();
  Object.defineProperty(viewport, "animate", { configurable: true, value: animate });
  document.body.append(viewport);
  return { viewport, animate };
}

it("rings the landed message without forcing a synchronous layout", () => {
  const node = addMessage("mounted-message");
  let offsetReads = 0;
  // The old restart trick read offsetWidth, which lays out the whole thread.
  Object.defineProperty(node, "offsetWidth", {
    configurable: true,
    get() { offsetReads += 1; return 0; },
  });

  expect(flashMessage("mounted-message")).toBe(true);

  expect(offsetReads).toBe(0);
  expect(node.classList.contains("assistant-history-located")).toBe(true);
  const first = ["assistant-history-located-a", "assistant-history-located-b"]
    .find(name => node.classList.contains(name));
  expect(first).toBeTruthy();

  // A second locate restarts the bloom by swapping the animation name.
  expect(flashMessage("mounted-message")).toBe(true);
  const second = ["assistant-history-located-a", "assistant-history-located-b"]
    .find(name => node.classList.contains(name));
  expect(second).toBeTruthy();
  expect(second).not.toBe(first);
  expect(offsetReads).toBe(0);
});

it("softens a long jump with an opacity-only viewport fade", () => {
  const { viewport, animate } = addViewport();
  const node = addMessage("old-message", 5_000, viewport);

  expect(flashMessage("old-message")).toBe(true);

  // Far jumps cannot smooth-scroll across a long thread without a slow crawl.
  expect(node.scrollIntoView).toHaveBeenCalledWith({ block: "start", behavior: "auto" });
  expect(animate).toHaveBeenCalledTimes(1);
  const [keyframes, options] = animate.mock.calls[0] as [Array<Record<string, unknown>>, Record<string, unknown>];
  expect(options.duration).toBe(180);
  // Only compositor properties: a box-shadow/background here would repaint
  // every message frame by frame.
  for (const frame of keyframes) {
    for (const key of Object.keys(frame)) expect(["opacity", "transform"]).toContain(key);
  }
});

it("keeps a nearby hop smooth and does not fade the viewport", () => {
  const { viewport, animate } = addViewport();
  const node = addMessage("nearby-message", 120, viewport);

  expect(flashMessage("nearby-message")).toBe(true);

  expect(node.scrollIntoView).toHaveBeenCalledWith({ block: "start", behavior: "smooth" });
  expect(animate).not.toHaveBeenCalled();
});

it("drops the motion but keeps the landing marker under reduced motion", () => {
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  const { viewport, animate } = addViewport();
  const node = addMessage("old-message", 5_000, viewport);

  expect(flashMessage("old-message")).toBe(true);

  expect(node.scrollIntoView).toHaveBeenCalledWith({ block: "start", behavior: "auto" });
  expect(animate).not.toHaveBeenCalled();
  expect(node.classList.contains("assistant-history-located")).toBe(true);
});

it("walks, lands and returns to idle so the rail can measure again", () => {
  vi.useFakeTimers();
  const phases: string[] = [];
  const unsubscribe = subscribeLocatePhase((_thread, phase) => phases.push(phase));

  const generation = beginLocate("thread-1");
  expect(getLocatePhase("thread-1")).toBe("walking");

  settleLocate("thread-1", generation, true);
  expect(getLocatePhase("thread-1")).toBe("landing");

  vi.advanceTimersByTime(400);
  expect(getLocatePhase("thread-1")).toBe("idle");
  expect(phases).toEqual(["walking", "landing", "idle"]);

  // Another thread's rail is never frozen by this one.
  expect(getLocatePhase("thread-2")).toBe("idle");
  unsubscribe();
});

it("ignores a settle from a walk that a newer locate replaced", () => {
  vi.useFakeTimers();
  const first = beginLocate("thread-1");
  const second = beginLocate("thread-1");

  settleLocate("thread-1", first, true);
  expect(getLocatePhase("thread-1")).toBe("walking");

  settleLocate("thread-1", second, false);
  expect(getLocatePhase("thread-1")).toBe("idle");
});

it("reports walking while the walk runs, then landing after the hit", async () => {
  const loadUntil = vi.fn(async () => {
    addMessage("old-message");
    return "found" as const;
  });
  registerHistoryLocator({
    threadId: "thread-1",
    hasMore: () => true,
    loadEarlier: async () => "loaded" as const,
    loadUntil,
  });

  const pending = locateInChat("old-message", "thread-1");
  expect(getLocatePhase("thread-1")).toBe("walking");

  await expect(pending).resolves.toBe("located");
  expect(getLocatePhase("thread-1")).toBe("landing");
  expect(loadUntil).toHaveBeenCalledTimes(1);
});
