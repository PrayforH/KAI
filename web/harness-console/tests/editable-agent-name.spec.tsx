// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { beforeEach, afterEach, it, expect, vi } from "vitest";
import { EditableAgentName } from "../src/components/agent-studio/editable-agent-name";
let host: HTMLDivElement; let root: Root;
const change = vi.fn();
beforeEach(async () => {
  Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
  change.mockReset(); host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  await act(async () => root.render(<EditableAgentName name="审计内控助手" disabled={false} onChange={change} />));
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); });
async function edit(value: string) {
  await act(async () => host.querySelector("button")!.click());
  const input = host.querySelector("input")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  return input;
}
it("confirms a trimmed display name on Enter", async () => {
  const input = await edit("  新名称  ");
  await act(async () => input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true })));
  expect(change).toHaveBeenCalledExactlyOnceWith("新名称");
  expect(host.querySelector("input")).toBeNull();
});
it("commits on blur and cancels Escape or blank input", async () => {
  let input = await edit("更名");
  await act(async () => input.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
  expect(change).not.toHaveBeenCalled();
  input = await edit("   "); await act(async () => input.blur());
  expect(change).not.toHaveBeenCalled();
  input = await edit("失焦更名"); await act(async () => input.blur());
  expect(change).toHaveBeenCalledExactlyOnceWith("失焦更名");
});
it("does not submit an IME confirmation or expose editing to read-only users", async () => {
  const input = await edit("中文名称");
  await act(async () => {
    input.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true }));
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    input.dispatchEvent(new CompositionEvent("compositionend", { bubbles: true }));
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  });
  expect(change).not.toHaveBeenCalled();
  await act(async () => root.render(<EditableAgentName name="审计内控助手" disabled onChange={change} />));
  expect(host.querySelector("button, input")).toBeNull();
});
