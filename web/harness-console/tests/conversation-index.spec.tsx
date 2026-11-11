// @vitest-environment jsdom
import { act, useRef } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ConversationIndex } from "../src/components/conversation-index";
import { beginLocate, resetLocatePhases, settleLocate } from "../src/lib/locate-signal";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let host: HTMLDivElement;
let root: Root;
let scroll: ReturnType<typeof vi.fn>;
let turnRectReads = 0;
function Harness({ id = "task-a", prompts = ["第一问", "第二问", "第三问"], total, loadEarlier = async () => false }: {
  id?: string; prompts?: string[]; total?: number; loadEarlier?: (pages?: number) => Promise<boolean>;
}) {
  const frame = useRef<HTMLDivElement>(null);
  return <div ref={frame}><div className="aui-thread-viewport" ref={node => {
    if (!node) return;
    node.scrollTo = scroll as unknown as HTMLElement["scrollTo"];
    node.getBoundingClientRect = () => ({ top: 10 } as DOMRect);
  }}>{prompts.map((prompt, i) => <article key={id + i} data-turn-id={id + i} data-turn-label={prompt} tabIndex={-1} ref={node => {
    if (node) node.getBoundingClientRect = () => { turnRectReads += 1; return { top: 10 + i * 300 - (node.parentElement?.scrollTop ?? 0) } as DOMRect; };
  }}>{prompt}<div data-turn-answer={`关于${prompt}的回答摘要`} /></article>)}</div><ConversationIndex frame={frame} threadId={id} pagination={total === undefined ? undefined : { total, hasMore: total > prompts.length, loadEarlier }} /></div>;
}
beforeEach(() => {
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  vi.stubGlobal("requestAnimationFrame", (fn: FrameRequestCallback) => setTimeout(fn, 0));
  vi.stubGlobal("cancelAnimationFrame", clearTimeout);
  vi.stubGlobal("matchMedia", () => ({ matches: false }));
  scroll = vi.fn(); turnRectReads = 0; host = document.createElement("div"); document.body.append(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); resetLocatePhases(); vi.unstubAllGlobals(); });
async function render(props: Parameters<typeof Harness>[0] = {}) { await act(async () => { root.render(<Harness {...props} />); await new Promise(resolve => setTimeout(resolve, 10)); }); }
it("indexes each user turn, previews labels and scrolls only its viewport", async () => {
  await render();
  const buttons = host.querySelectorAll<HTMLButtonElement>("nav button");
  expect(buttons).toHaveLength(3);
  // The rail paints statically: no entrance animation is armed.
  expect(host.querySelector(".conversation-index-rail")?.getAttribute("data-revealing")).toBeNull();
  expect(buttons[0].getAttribute("aria-current")).toBe("location");
  act(() => buttons[1].focus());
  expect(host.querySelector(".conversation-index-preview")?.textContent).toContain("关于第二问的回答摘要");
  expect(buttons[1].style.getPropertyValue("--index-line-width")).toBe("19px");
  expect(buttons[0].style.getPropertyValue("--index-line-width")).toBe("14px");
  act(() => buttons[2].click());
  expect(scroll).toHaveBeenCalledWith({ top: 568, behavior: "smooth" });
  expect(document.activeElement?.getAttribute("data-turn-id")).toBe("task-a2");
  const viewport = host.querySelector<HTMLElement>(".aui-thread-viewport")!;
  await act(async () => {
    viewport.scrollTop = 320;
    viewport.dispatchEvent(new Event("scroll"));
    // Scroll tracking is coalesced into one animation frame.
    await new Promise(resolve => setTimeout(resolve, 10));
  });
  expect(buttons[1].getAttribute("aria-current")).toBe("location");
});
it("supports keyboard navigation, updates branches and clears empty conversations", async () => {
  await render();
  act(() => { const first = host.querySelector<HTMLButtonElement>("nav button")!; first.focus(); first.dispatchEvent(new KeyboardEvent("keydown", { key: "End", bubbles: true })); });
  expect(document.activeElement?.getAttribute("aria-label")).toContain("第三问");
  await render({ id: "task-b", prompts: ["另一个分支"] });
  expect(host.querySelectorAll("nav button")).toHaveLength(1);
  expect(host.querySelector("nav button")?.getAttribute("aria-label")).toContain("另一个分支");
  await render({ id: "empty", prompts: [] });
  expect(host.querySelector("nav")).toBeNull();
});

it("waits for the turn count and renders every tick at once", async () => {
  // The history endpoint reports 5 turns while only 3 are loaded: the rail shows
  // all five together instead of growing from three to five.
  await render({ total: 5 });
  expect(host.querySelectorAll("nav button")).toHaveLength(5);
  expect(host.querySelectorAll(".conversation-index-pending")).toHaveLength(2);

  // Nothing is indexed until that count is known.
  await render({ id: "task-empty", prompts: [], total: 0 });
  expect(host.querySelector("nav")).toBeNull();

  // A first-page reload clears the count for a moment; the rail keeps its ticks.
  await render({ id: "task-a", prompts: ["第一问", "第二问", "第三问"], total: 5 });
  expect(host.querySelectorAll("nav button")).toHaveLength(5);
  await render({ prompts: [], total: 0 });
  expect(host.querySelectorAll("nav button")).toHaveLength(5);
});

it("tracks the active turn without measuring every turn on every scroll", async () => {
  await render();
  const viewport = host.querySelector<HTMLElement>(".aui-thread-viewport")!;
  const buttons = host.querySelectorAll<HTMLButtonElement>("nav button");
  // Let the mount-time scan (effect + one coalesced mutation frame) settle.
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 40)); });
  // Offsets were measured once when the turn list was scanned.
  expect(turnRectReads).toBeGreaterThanOrEqual(3);
  turnRectReads = 0;

  await act(async () => {
    viewport.scrollTop = 620;
    viewport.dispatchEvent(new Event("scroll"));
    await new Promise(resolve => setTimeout(resolve, 10));
  });

  // Scroll only compares cached offsets: no layout read per turn.
  expect(turnRectReads).toBe(0);
  expect(buttons[2].getAttribute("aria-current")).toBe("location");
});

it("skips the per-turn scan while a locate is landing, then measures once", async () => {
  await render();
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 40)); });
  turnRectReads = 0;

  const generation = beginLocate("task-a");
  // The re-import lands turns while the scroll is still settling; scanning them
  // in those frames was the hitch the landing animation sat on top of.
  await act(async () => {
    const extra = document.createElement("article");
    extra.setAttribute("data-turn-id", "task-a3");
    extra.setAttribute("data-turn-label", "第四问");
    host.querySelector(".aui-thread-viewport")!.append(extra);
    await new Promise(resolve => setTimeout(resolve, 30));
  });
  expect(turnRectReads).toBe(0);

  await act(async () => {
    settleLocate("task-a", generation, true);
    await new Promise(resolve => setTimeout(resolve, 420));
  });
  // One scan after the landing, not one per mutation frame.
  expect(turnRectReads).toBeGreaterThanOrEqual(3);
  expect(host.querySelectorAll("nav button")).toHaveLength(4);
});

it("pulses the turn that owns the landed message", async () => {
  await render();
  const turn = host.querySelector<HTMLElement>('[data-turn-id="task-a1"]')!;
  // An assistant reply is the turn's following sibling, not its child.
  const reply = document.createElement("div");
  reply.setAttribute("data-message-id", "assistant-reply-1");
  turn.after(reply);

  await act(async () => {
    window.dispatchEvent(new CustomEvent("harness:chat-located", { detail: { messageId: "assistant-reply-1" } }));
    await new Promise(resolve => setTimeout(resolve, 0));
  });

  const buttons = host.querySelectorAll<HTMLButtonElement>("nav button");
  // The reply belongs to the turn before it, not to the next prompt.
  expect(buttons[1].getAttribute("data-located")).toBe("true");
  expect(buttons[0].getAttribute("data-located")).toBeNull();
  expect(buttons[2].getAttribute("data-located")).toBeNull();
});
