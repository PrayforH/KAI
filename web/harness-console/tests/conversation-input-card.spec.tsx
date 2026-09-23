// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { ConversationInputCard } from "../src/components/conversation-input-card";
import type { ConversationInput } from "../src/lib/conversation-input";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root; let host: HTMLDivElement;
afterEach(() => { act(() => root?.unmount()); host?.remove(); });
const input: ConversationInput = {version: 1, title: "选择范围", questions: [{id: "scope", label: "修改哪些内容", type: "multi", options: ["UI", "接口"]}]};
function mount(onSubmit = vi.fn(), disabled = false) {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  act(() => root.render(<ConversationInputCard input={input} disabled={disabled} onSubmit={onSubmit} />)); return onSubmit;
}
function submit() { return [...host.querySelectorAll("button")].find(b => b.textContent?.includes("提交") || b.textContent?.includes("发送中"))!; }
it("requires an actual selection, supports multiple answers and prevents duplicate submission", async () => {
  let finish!: () => void;
  const send = mount(vi.fn(() => new Promise<void>(resolve => {finish = resolve;})));
  expect(submit().disabled).toBe(true);
  expect(host.querySelectorAll("input:checked")).toHaveLength(0);
  act(() => host.querySelectorAll<HTMLInputElement>("input[type=checkbox]").forEach(el => el.click()));
  await act(async () => { submit().click(); submit().click(); });
  expect(send).toHaveBeenCalledTimes(1); expect(send.mock.calls[0][0]).toContain("UI；接口");
  await act(async () => finish()); expect(host.textContent).toContain("已提交");
});
it("preserves typed answers after submission failure and allows retry", async () => {
  const send = mount(vi.fn().mockRejectedValueOnce(new Error("连接断开")).mockResolvedValue(undefined));
  const text = host.querySelector<HTMLInputElement>("input[type=text]")!;
  act(() => { Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(text, "只改文案"); text.dispatchEvent(new Event("input", {bubbles: true})); });
  await act(async () => submit().click());
  expect(host.querySelector('[role="alert"]')?.textContent).toBe("连接断开"); expect(text.value).toBe("只改文案");
  await act(async () => submit().click()); expect(send).toHaveBeenCalledTimes(2);
});
it("does not submit when the conversation is locked", () => {
  const send = mount(vi.fn(), true); act(() => submit().click()); expect(send).not.toHaveBeenCalled();
});
