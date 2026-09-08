// @vitest-environment jsdom
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { PromptQueue } from "../src/components/prompt-queue";
import type { QueuedPrompt } from "../src/lib/composer-interactions";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root;
let host: HTMLDivElement;
afterEach(() => { act(() => root?.unmount()); host?.remove(); });
const seed: QueuedPrompt[] = [{ id: "a", text: "first", attachments: [] }, { id: "b", text: "second", attachments: [] }];
function mount(sendingIds: string[] = []) {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  const change = vi.fn(); const guide = vi.fn(); const send = vi.fn();
  function Harness() {
    const [items, setItems] = useState(seed);
    return <PromptQueue items={items} paused busy canSteer sendingIds={sendingIds} onChange={(next) => { change(next); setItems(next); }} onPause={() => {}} onGuide={guide} onSend={send} />;
  }
  act(() => root.render(<Harness />)); return { change, guide, send };
}
function click(label: string) { act(() => (host.querySelector(`[aria-label="${label}"]`) as HTMLButtonElement).click()); }
it("edits in place without losing order or attachments, and cancel keeps original", () => {
  const { change } = mount();
  act(() => ([...host.querySelectorAll("button")].find((b) => b.textContent === "编辑") as HTMLButtonElement).click());
  const input = host.querySelector("textarea")!;
  act(() => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(input, "中文补充"); input.dispatchEvent(new Event("input", { bubbles: true })); });
  act(() => ([...host.querySelectorAll("button")].find((b) => b.textContent === "保存") as HTMLButtonElement).click());
  expect(change.mock.lastCall?.[0].map((item: QueuedPrompt) => [item.id, item.text])).toEqual([["a", "中文补充"], ["b", "second"]]);
  act(() => ([...host.querySelectorAll("button")].find((b) => b.textContent === "编辑") as HTMLButtonElement).click());
  act(() => ([...host.querySelectorAll("button")].find((b) => b.textContent === "取消") as HTMLButtonElement).click());
  expect(host.querySelector("textarea")).toBeNull(); expect(change).toHaveBeenCalledTimes(1);
});
it("reorders and deletes the intended queued message by identity", () => {
  const { change } = mount(); click("上移第 2 条");
  expect(change.mock.lastCall?.[0].map((item: QueuedPrompt) => item.id)).toEqual(["b", "a"]);
  click("删除第 1 条"); expect(change.mock.lastCall?.[0].map((item: QueuedPrompt) => item.id)).toEqual(["a"]);
});
it("prevents mutation of a message while its guidance acknowledgement is pending", () => {
  const { change } = mount(["a"]); click("删除第 1 条"); click("下移第 1 条");
  expect(change).not.toHaveBeenCalled();
});
