// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { writeMessageToClipboard } from "../src/components/agent-thread";
const clipboardDescriptor = Object.getOwnPropertyDescriptor(navigator, "clipboard");
const commandDescriptor = Object.getOwnPropertyDescriptor(document, "execCommand");
let button: HTMLButtonElement;
beforeEach(() => {
  button = document.createElement("button"); document.body.append(button); button.focus();
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: undefined });
});
afterEach(() => {
  document.body.replaceChildren();
  if (clipboardDescriptor) Object.defineProperty(navigator, "clipboard", clipboardDescriptor);
  else Reflect.deleteProperty(navigator, "clipboard");
  if (commandDescriptor) Object.defineProperty(document, "execCommand", commandDescriptor);
  else Reflect.deleteProperty(document, "execCommand");
});
it("copies normalized message text through the HTTP fallback and restores focus", async () => {
  let selected = "";
  const command = vi.fn(() => {
    const text = document.activeElement as HTMLTextAreaElement;
    selected = text.value.slice(text.selectionStart, text.selectionEnd);
    return true;
  });
  Object.defineProperty(document, "execCommand", { configurable: true, value: command });
  expect(await writeMessageToClipboard(" 第一行\r\n\r\n\r\n第二行 \n")).toBe(true);
  expect(selected).toBe("第一行\n\n第二行"); expect(command).toHaveBeenCalledWith("copy");
  expect(document.activeElement).toBe(button); expect(document.querySelector("textarea")).toBeNull();
});
it("falls back after a denied modern write and returns actual failure", async () => {
  const modern = vi.fn().mockRejectedValue(new Error("denied"));
  Object.defineProperty(navigator, "clipboard", { configurable: true, value: { writeText: modern } });
  Object.defineProperty(document, "execCommand", { configurable: true, value: vi.fn(() => false) });
  expect(await writeMessageToClipboard("原文")).toBe(false); expect(modern).toHaveBeenCalledWith("原文");
  expect(document.activeElement).toBe(button); expect(document.querySelector("textarea")).toBeNull();
});
it("reports unsupported copying without leaving a temporary textarea", async () => {
  Object.defineProperty(document, "execCommand", { configurable: true, value: undefined });
  expect(await writeMessageToClipboard("原文")).toBe(false);
  expect(document.activeElement).toBe(button); expect(document.querySelector("textarea")).toBeNull();
});
