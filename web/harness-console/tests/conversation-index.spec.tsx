// @vitest-environment jsdom
import { act, useRef } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ConversationIndex } from "../src/components/conversation-index";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let host: HTMLDivElement;
let root: Root;
let scroll: ReturnType<typeof vi.fn>;
function Harness({ id = "task-a", prompts = ["第一问", "第二问", "第三问"] }: { id?: string; prompts?: string[] }) {
  const frame = useRef<HTMLDivElement>(null);
  return <div ref={frame}><div className="aui-thread-viewport" ref={node => {
    if (!node) return;
    node.scrollTo = scroll;
    node.getBoundingClientRect = () => ({ top: 10 } as DOMRect);
  }}>{prompts.map((prompt, i) => <article key={id + i} data-turn-id={id + i} data-turn-label={prompt} tabIndex={-1} ref={node => {
    if (node) node.getBoundingClientRect = () => ({ top: 10 + i * 300 - (node.parentElement?.scrollTop ?? 0) } as DOMRect);
  }}>{prompt}<div data-turn-answer={`关于${prompt}的回答摘要`} /></article>)}</div><ConversationIndex frame={frame} threadId={id} /></div>;
}
beforeEach(() => {
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  vi.stubGlobal("requestAnimationFrame", (fn: FrameRequestCallback) => setTimeout(fn, 0));
  vi.stubGlobal("cancelAnimationFrame", clearTimeout);
  vi.stubGlobal("matchMedia", () => ({ matches: false }));
  scroll = vi.fn(); host = document.createElement("div"); document.body.append(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); vi.unstubAllGlobals(); });
async function render(props: Parameters<typeof Harness>[0] = {}) { await act(async () => { root.render(<Harness {...props} />); await new Promise(resolve => setTimeout(resolve, 10)); }); }
it("indexes each user turn, previews labels and scrolls only its viewport", async () => {
  await render();
  const buttons = host.querySelectorAll<HTMLButtonElement>("nav button");
  expect(buttons).toHaveLength(3);
  expect(buttons[0].getAttribute("aria-current")).toBe("location");
  act(() => buttons[1].focus());
  expect(host.querySelector(".conversation-index-preview")?.textContent).toContain("关于第二问的回答摘要");
  expect(buttons[1].style.getPropertyValue("--index-line-width")).toBe("16px");
  expect(buttons[0].style.getPropertyValue("--index-line-width")).toBe("12px");
  act(() => buttons[2].click());
  expect(scroll).toHaveBeenCalledWith({ top: 568, behavior: "smooth" });
  expect(document.activeElement?.getAttribute("data-turn-id")).toBe("task-a2");
  const viewport = host.querySelector<HTMLElement>(".aui-thread-viewport")!;
  act(() => { viewport.scrollTop = 320; viewport.dispatchEvent(new Event("scroll")); });
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
