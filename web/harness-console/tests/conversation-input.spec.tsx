// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { AssistantRuntimeProvider, useLocalRuntime } from "@assistant-ui/react";
import { afterEach, expect, it, vi } from "vitest";
import { ConversationInput } from "../src/components/conversation-input";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
let root: Root;
let container: HTMLDivElement;
afterEach(async () => { if (root) await act(async () => root.unmount()); container?.remove(); });

it("preserves Chinese composition and reserves candidate Enter before normal submit", async () => {
  const submit = vi.fn();
  function Fixture() {
    const runtime = useLocalRuntime({ async *run() { yield { content: [{ type: "text" as const, text: "ok" }] }; } });
    return <AssistantRuntimeProvider runtime={runtime}>
      <ConversationInput onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); submit(); } }} />
    </AssistantRuntimeProvider>;
  }
  container = document.createElement("div"); document.body.appendChild(container); root = createRoot(container);
  await act(async () => root.render(<Fixture />));
  const input = container.querySelector("textarea")!;
  const setValue = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
  await act(async () => {
    input.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true }));
    setValue.call(input, "zhong");
    input.dispatchEvent(new InputEvent("input", { bubbles: true, isComposing: true, inputType: "insertCompositionText" }));
  });
  expect(input.value).toBe("zhong");
  await act(async () => input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, isComposing: true })));
  expect(submit).not.toHaveBeenCalled();
  await act(async () => {
    setValue.call(input, "中文输入");
    input.dispatchEvent(new InputEvent("input", { bubbles: true, isComposing: true, inputType: "insertCompositionText" }));
    input.dispatchEvent(new CompositionEvent("compositionend", { bubbles: true, data: "中文输入" }));
    // Safari can send the confirmation Enter after compositionend with isComposing=false.
    const confirmation = new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true });
    input.dispatchEvent(confirmation);
    expect(confirmation.defaultPrevented).toBe(true);
  });
  expect(input.value).toBe("中文输入");
  expect(submit).not.toHaveBeenCalled();
  await new Promise((resolve) => setTimeout(resolve, 90));
  await act(async () => input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true, cancelable: true })));
  expect(submit).toHaveBeenCalledOnce();
});
