// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { attachConversationScroll } from "../src/lib/conversation-scroll";
let onResize: () => void;
let queued: Map<number, FrameRequestCallback>;
let serial = 0;
let clock = 0;
let viewport: HTMLDivElement;
let height: number;
let client: number;
let top: number;
let control: ReturnType<typeof attachConversationScroll>;
let observe: ReturnType<typeof vi.fn>;
let unobserve: ReturnType<typeof vi.fn>;
const flush = async () => {
  await Promise.resolve();
  const callbacks = [...queued.values()]; queued.clear();
  for (const callback of callbacks) callback(clock += 16);
};
const settle = async () => { for (let i = 0; i < 80 && queued.size; i++) await flush(); };
const scrollEvent = () => viewport.dispatchEvent(new Event("scroll"));
const grow = (size = 80) => { height += size; onResize(); };
beforeEach(() => {
  queued = new Map(); clock = 0;
  vi.stubGlobal("matchMedia", () => ({ matches: false }));
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => { queued.set(++serial, cb); return serial; });
  vi.stubGlobal("cancelAnimationFrame", (id: number) => queued.delete(id));
  observe = vi.fn(); unobserve = vi.fn();
  vi.stubGlobal("ResizeObserver", class {
    constructor(cb: () => void) { onResize = cb; }
    observe = observe; unobserve = unobserve; disconnect = vi.fn();
  });
  viewport = document.createElement("div"); document.body.appendChild(viewport);
  height = 1200; client = 500; top = 0;
  Object.defineProperties(viewport, {
    scrollHeight: { get: () => height }, clientHeight: { get: () => client },
    scrollTop: { get: () => top, set: (value: number) => { top = value; } },
  });
  viewport.scrollTo = vi.fn((options: ScrollToOptions) => {
    top = Math.max(0, Math.min(options.top ?? top, height - client)); scrollEvent();
  }) as typeof viewport.scrollTo;
  control = attachConversationScroll(viewport);
});
afterEach(() => { control.dispose(); viewport.remove(); vi.unstubAllGlobals(); });
it("follows streamed output and late layout growth once per frame", async () => {
  await flush(); expect(top).toBe(700);
  const calls = vi.mocked(viewport.scrollTo).mock.calls.length;
  height += 320; onResize();
  await flush(); expect(top).toBe(height - client);
  expect(vi.mocked(viewport.scrollTo).mock.calls.length).toBe(calls + 1);
  await settle(); expect(top).toBe(height - client);
  const message = document.createElement("article"); viewport.appendChild(message);
  await flush(); expect(observe).toHaveBeenCalledWith(message);
  grow(900); await settle(); expect(top).toBe(height - client);
  message.remove(); await flush(); expect(unobserve).toHaveBeenCalledWith(message);
});
it("does not mistake process collapse or composer resizing for reader scroll-up", async () => {
  await flush(); height -= 400; top -= 400; scrollEvent(); onResize(); await flush();
  grow(300); await settle(); expect(top).toBe(height - client);
  client -= 100; onResize(); await settle(); expect(top).toBe(height - client);
});
it("pauses before queued layout scroll on wheel-up and resumes at the bottom", async () => {
  await flush(); grow(); viewport.dispatchEvent(new WheelEvent("wheel", { deltaY: -120 }));
  top -= 120; scrollEvent(); const readingTop = top;
  await flush(); grow(900); await flush(); expect(top).toBe(readingTop);
  top = height - client; scrollEvent(); grow(); await settle(); expect(top).toBe(height - client);
});
it("preserves history prepend position; a new run resumes follow", async () => {
  await flush(); top = 100; scrollEvent();
  height += 1000; top += 1000; scrollEvent(); onResize(); await flush(); expect(top).toBe(1100);
  grow(); await flush(); expect(top).toBe(1100);
  control.resume(); await settle(); expect(top).toBe(height - client);
});
it("leaves nested result scrolling independent of the conversation", async () => {
  await flush(); const result = document.createElement("pre"); result.style.overflowY = "auto";
  Object.defineProperties(result, { scrollHeight: { value: 500 }, clientHeight: { value: 100 } });
  viewport.appendChild(result); result.dispatchEvent(new WheelEvent("wheel", { deltaY: -100, bubbles: true }));
  grow(); await settle(); expect(top).toBe(height - client);
});
it("supports keyboard pause, the bottom button, and cleanup", async () => {
  await flush(); viewport.dispatchEvent(new KeyboardEvent("keydown", { key: "PageUp" }));
  const readingTop = top; grow(); await flush(); expect(top).toBe(readingTop);
  const button = document.createElement("button"); button.className = "aui-thread-scroll-to-bottom";
  viewport.appendChild(button); button.click(); await settle(); expect(top).toBe(height - client);
  control.dispose(); grow(); await flush(); expect(top).toBe(height - client - 80);
});

it("follows the actual layout without leaving an animation backlog", async () => {
  await flush(); grow(300); await flush(); expect(top).toBe(height - client);
  grow(100); await flush(); expect(top).toBe(height - client);
  expect(queued.size).toBe(0);
});

it("shows the jump only when the reader leaves the latest message, not when a draft grows", async () => {
  control.dispose(); const visibility = vi.fn();
  control = attachConversationScroll(viewport, visibility);
  await flush(); grow(250); await flush();
  expect(visibility).not.toHaveBeenCalled();
  await settle(); expect(top).toBe(height - client);
  viewport.dispatchEvent(new WheelEvent("wheel", { deltaY: -200 })); top -= 200; scrollEvent();
  expect(visibility).toHaveBeenLastCalledWith(true);
  const calls = visibility.mock.calls.length;
  grow(120); await settle(); expect(visibility).toHaveBeenCalledTimes(calls);
  control.resume(); expect(visibility).toHaveBeenLastCalledWith(false);
  await settle(); grow(1500); await settle();
  expect(visibility.mock.calls.map(call => call[0])).toEqual([true, false]);
});

it("corrects the viewport before paint when completion collapses the process", async () => {
  await flush();
  height -= 350;
  onResize();
  expect(top).toBe(height - client);
  expect(queued.size).toBe(0);
});
