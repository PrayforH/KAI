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
function mount(onSubmit = vi.fn(), disabled = false, data = input) {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  act(() => root.render(<ConversationInputCard input={data} disabled={disabled} onSubmit={onSubmit} />)); return onSubmit;
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

const pagedInput: ConversationInput = { ...input, questions: [
  { id: "scope", label: "修改哪些内容", type: "single", options: ["UI", "接口"] },
  { id: "time", label: "何时执行", type: "single", options: ["今天", "明天"] },
] };
function button(label: string) {
  return [...host.querySelectorAll("button")].find(el => el.textContent?.includes(label))!;
}
function key(target: Element, key: string, options: KeyboardEventInit = {}) {
  act(() => target.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...options })));
}
it("retains answers across pages and requires every question before sending", async () => {
  const send = mount(vi.fn(), false, pagedInput);
  expect(host.querySelectorAll("fieldset")).toHaveLength(1);
  expect(button("下一步").disabled).toBe(true);
  act(() => host.querySelector<HTMLButtonElement>('[aria-label="下一题"]')!.click());
  act(() => host.querySelector<HTMLInputElement>('input[type="radio"]')!.click());
  expect(submit().disabled).toBe(true);
  act(() => host.querySelector<HTMLButtonElement>('[aria-label="上一题"]')!.click());
  act(() => host.querySelector<HTMLInputElement>('input[type="radio"]')!.click());
  act(() => button("下一步").click());
  expect(host.querySelector<HTMLInputElement>('input[type="radio"]')!.checked).toBe(true);
  expect(send).not.toHaveBeenCalled();
  await act(async () => submit().click());
  expect(send).toHaveBeenCalledWith("选择范围\n\n修改哪些内容\nUI\n\n何时执行\n今天");
});
it("scopes numeric and navigation shortcuts to the card without sending on selection", () => {
  const send = mount(vi.fn(), false, pagedInput);
  const card = host.querySelector("section")!;
  key(card, "2");
  expect(host.querySelectorAll<HTMLInputElement>('input[type="radio"]')[1].checked).toBe(true);
  expect(send).not.toHaveBeenCalled();
  key(card, "ArrowRight");
  expect(host.textContent).toContain("何时执行");
  key(card, "1", { ctrlKey: true });
  key(card, "1", { isComposing: true });
  expect(host.querySelectorAll("input:checked")).toHaveLength(0);
  key(host.querySelector('input[type="text"]')!, "1");
  expect(host.querySelectorAll("input:checked")).toHaveLength(0);
  key(card, "ArrowLeft");
  expect(host.querySelectorAll<HTMLInputElement>('input[type="radio"]')[1].checked).toBe(true);
});
it("preserves answers while collapsed and blocks shortcuts when disabled", () => {
  mount();
  const card = host.querySelector("section")!;
  key(card, "1");
  act(() => host.querySelector<HTMLButtonElement>('[aria-expanded]')!.click());
  key(card, "2");
  act(() => host.querySelector<HTMLButtonElement>('[aria-expanded]')!.click());
  expect(host.querySelectorAll("input:checked")).toHaveLength(1);
  act(() => root.render(<ConversationInputCard input={input} disabled onSubmit={vi.fn()} />));
  key(card, "2");
  expect(host.querySelectorAll("input:checked")).toHaveLength(1);
});

it("skips unanswered questions while preserving answers and preventing duplicate sends", async () => {
  let finish!: () => void;
  const send = mount(vi.fn(() => new Promise<void>(resolve => { finish = resolve; })), false, pagedInput);
  act(() => host.querySelector<HTMLInputElement>('input[type="radio"]')!.click());
  await act(async () => { button("跳过").click(); button("跳过").click(); });
  expect(send).toHaveBeenCalledTimes(1);
  expect(send.mock.calls[0][0]).toContain("修改哪些内容\nUI");
  expect(send.mock.calls[0][0]).toContain("何时执行\n已跳过");
  await act(async () => finish());
  expect(host.textContent).toContain("已提交");
});
it("allows skipping an empty form but not while the conversation is locked", async () => {
  const send = mount(vi.fn(), true);
  act(() => button("跳过").click());
  expect(send).not.toHaveBeenCalled();
  act(() => root.render(<ConversationInputCard input={input} disabled={false} onSubmit={send} />));
  await act(async () => button("跳过").click());
  expect(send).toHaveBeenCalledTimes(1);
});
it("masks inferred password entry, submits the original value, and clears it after success",async()=>{
 const send=mount(vi.fn(),false,{version:1,title:'重新授权',questions:[{id:'pwd',label:'数据库密码',type:'text',options:[]}]});
 const field=host.querySelector<HTMLInputElement>('input[type=password]')!;
 expect(field).not.toBeNull();expect(field.autocomplete).toBe('off');
 const password='Example-Pass-42!';
 act(()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(field,password);field.dispatchEvent(new Event('input',{bubbles:true}));});
 key(field,'ArrowLeft');expect(field.type).toBe('password');
 await act(async()=>submit().click());
 expect(send).toHaveBeenCalledWith('重新授权\n\n数据库密码\n'+password);
 expect(field.value).toBe('');expect(host.innerHTML).not.toContain(password);
 expect(field.placeholder).toBe('已提交，内容已隐藏');
});
it('redacts password echoes in failed submission errors while retaining a masked retry value',async()=>{
 const password='Example-Pass-42!';const send=mount(vi.fn().mockRejectedValue(new Error('无法连接：'+password)),false,{version:1,title:'连接',questions:[{id:'pwd',label:'密码',type:'password',options:[]}]});
 const field=host.querySelector<HTMLInputElement>('input[type=password]')!;
 act(()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(field,password);field.dispatchEvent(new Event('input',{bubbles:true}));});
 await act(async()=>submit().click());expect(send).toHaveBeenCalledOnce();
 expect(host.querySelector('[role=alert]')?.textContent).toBe('无法连接：••••••••');expect(field.type).toBe('password');expect(field.value).toBe(password);
});
