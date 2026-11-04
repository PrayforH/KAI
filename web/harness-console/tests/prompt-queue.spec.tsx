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
  click("编辑第 1 条");
  const input = host.querySelector("textarea")!;
  act(() => { Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(input, "中文补充"); input.dispatchEvent(new Event("input", { bubbles: true })); });
  click("保存编辑");
  expect(change.mock.lastCall?.[0].map((item: QueuedPrompt) => [item.id, item.text])).toEqual([["a", "中文补充"], ["b", "second"]]);
  click("编辑第 1 条");
  click("取消编辑");
  expect(host.querySelector("textarea")).toBeNull(); expect(change).toHaveBeenCalledTimes(1);
});
it("reorders and deletes the intended queued message by identity", () => {
  const { change } = mount(); act(() => host.querySelectorAll("li")[1].dispatchEvent(new KeyboardEvent("keydown", {key: "ArrowUp", altKey: true, bubbles: true})));
  expect(change.mock.lastCall?.[0].map((item: QueuedPrompt) => item.id)).toEqual(["b", "a"]);
  click("删除第 1 条"); expect(change.mock.lastCall?.[0].map((item: QueuedPrompt) => item.id)).toEqual(["a"]);
});
it("prevents mutation of a message while its guidance acknowledgement is pending", () => {
  const { change } = mount(["a"]); click("删除第 1 条"); click("编辑第 1 条");
  act(() => host.querySelector("li")!.dispatchEvent(new KeyboardEvent("keydown", {key: "ArrowDown", altKey: true, bubbles: true})));
  expect(change).not.toHaveBeenCalled();
});


it("shows pending guidance without queue pause or action controls", () => {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  act(() => root.render(<PromptQueue items={[{id: "guidance", text: "继续转换", attachments: [], steerRunId: "run-1"}]} paused busy canSteer sendingIds={["guidance"]} onChange={vi.fn()} onPause={vi.fn()} onGuide={vi.fn()} onSend={vi.fn()} />));
  expect(host.textContent).toContain("等待接收");
  expect(host.querySelector("button")).toBeNull();
  expect(host.textContent).not.toContain("已暂停");
});

it("shows successive assistant sends as independent bubbles before a run is ready", () => {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  act(() => root.render(<PromptQueue chat items={seed.map(item => ({ ...item, intent: "steer" }))}
    paused={false} busy canSteer={false} sendingIds={[]} onChange={vi.fn()} onPause={vi.fn()} onGuide={vi.fn()} onSend={vi.fn()} />));
  expect([...host.querySelectorAll(".steering-user-message-bubble")].map(node => node.textContent)).toEqual(["first", "second"]);
  expect(host.querySelector(".prompt-queue")).toBeNull();
  expect(host.querySelector("button")).toBeNull();
});


it("keeps older uncertain sends collapsed even while a new run is active", () => {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  act(() => root.render(<PromptQueue chat items={[
    { ...seed[0], receipt: "unknown" }, { ...seed[1], receipt: "failed" },
  ]} paused busy canSteer={false} sendingIds={[]} onChange={vi.fn()} onPause={vi.fn()} onGuide={vi.fn()} onSend={vi.fn()} />));
  // A receipt already recorded means the ambiguity is from an earlier send;
  // it never pins over the composer again, run active or not.
  expect(host.querySelector(".assistant-pending-messages[aria-label='已发送的消息']")).toBeNull();
  const stuck = host.querySelector("details.assistant-pending-messages[data-stuck='true']") as HTMLDetailsElement;
  expect(stuck).not.toBeNull();
  expect(stuck.open).toBe(false);
  expect(stuck.querySelector("summary")?.textContent).toContain("未发送的补充消息（2）");
});

it("keeps the current run's own in-flight sends visible without receipt text or resend controls", () => {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  act(() => root.render(<PromptQueue chat items={seed} paused={false} busy canSteer={false} sendingIds={[]} onChange={vi.fn()} onPause={vi.fn()} onGuide={vi.fn()} onSend={vi.fn()} />));
  expect([...host.querySelectorAll(".steering-user-message-bubble")].map(node => node.textContent)).toEqual(["first", "second"]);
  expect(host.querySelector("button")).toBeNull();
  expect(host.querySelector("small")).toBeNull();
});

it("collapses unplaced sends behind one quiet line once nothing is running, with resend reachable", () => {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  const send = vi.fn();
  act(() => root.render(<PromptQueue chat items={[
    { ...seed[0], receipt: "unknown" }, { ...seed[1], receipt: "failed" },
  ]} paused busy={false} canSteer={false} sendingIds={[]} onChange={vi.fn()} onPause={vi.fn()} onGuide={vi.fn()} onSend={send} />));
  // Default collapsed: no bubble pinned over the composer.
  const stuck = host.querySelector("details.assistant-pending-messages[data-stuck='true']") as HTMLDetailsElement;
  expect(stuck).not.toBeNull();
  expect(stuck.open).toBe(false);
  expect(stuck.querySelector("summary")?.textContent).toContain("未发送的补充消息（2）");
  // Expanding keeps the original texts as unknown receipts and a resend affordance.
  act(() => { stuck.open = true; });
  expect([...stuck.querySelectorAll(".steering-user-message-bubble")].map(node => node.textContent)).toEqual(["first", "second"]);
  expect(stuck.querySelectorAll('[data-steering-status="unknown"]')).toHaveLength(2);
  const retries = [...stuck.querySelectorAll(".assistant-message-retry")] as HTMLButtonElement[];
  expect(retries.map(node => node.textContent)).toEqual(["重新发送", "重新发送"]);
  act(() => retries[1]!.click());
  expect(send).toHaveBeenCalledWith(expect.objectContaining({ id: "b", receipt: "failed" }));
});

it("keeps reconciled sends beside their original turn instead of the stuck section", () => {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  act(() => root.render(<PromptQueue chat items={[
    { ...seed[0], receipt: "unknown", historyRunId: "run-9" }, { ...seed[1] },
  ]} paused busy={false} canSteer={false} sendingIds={[]} onChange={vi.fn()} onPause={vi.fn()} onGuide={vi.fn()} onSend={vi.fn()} />));
  // The reconciled row portals beside its turn (fallback holder here); only the
  // unreconciled one collapses. A closed <details> keeps children in the DOM,
  // so assert ownership rather than absence.
  const bubbles = [...host.querySelectorAll(".steering-user-message-bubble")].map(node => node.textContent);
  expect(bubbles).toEqual(["first", "second"]);
  const stuck = host.querySelector("details.assistant-pending-messages[data-stuck='true']") as HTMLDetailsElement;
  expect(stuck.open).toBe(false);
  expect(stuck.querySelector(".steering-user-message-bubble")?.textContent).toBe("second");
  expect(stuck.querySelector("summary")?.textContent).toContain("未发送的补充消息（1）");
});
