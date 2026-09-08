// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { PanelResizeHandle } from "../src/components/panel-resize-handle";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let host: HTMLDivElement;
let root: Root;
beforeEach(() => {
  const stored = new Map<string, string>();
  vi.stubGlobal("localStorage", { getItem: (key: string) => stored.get(key) ?? null,
    setItem: (key: string, value: string) => stored.set(key, value) });
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); vi.unstubAllGlobals(); document.documentElement.removeAttribute("style"); });
function press(key: string) { act(() => host.querySelector('[role="separator"]')!.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }))); }
it("clamps and persists both keyboard limits, restores the saved width and resets on double click", () => {
  localStorage.setItem("agent-harness-sidebar-width", "320");
  act(() => root.render(<PanelResizeHandle panel="sidebar" />));
  expect(host.firstElementChild?.getAttribute("aria-valuenow")).toBe("320");
  press("End"); press("ArrowRight");
  expect(localStorage.getItem("agent-harness-sidebar-width")).toBe("380");
  expect(document.documentElement.style.getPropertyValue("--preferred-sidebar-width")).toBe("380px");
  press("Home"); press("ArrowLeft");
  expect(localStorage.getItem("agent-harness-sidebar-width")).toBe("220");
  act(() => host.firstElementChild!.dispatchEvent(new MouseEvent("dblclick", { bubbles: true })));
  expect(localStorage.getItem("agent-harness-sidebar-width")).toBe("264");
});
it("expands a right panel toward the left and ignores corrupt saved values", () => {
  localStorage.setItem("agent-harness-builder-width", "NaN");
  act(() => root.render(<PanelResizeHandle panel="builder" />));
  press("ArrowLeft");
  expect(localStorage.getItem("agent-harness-builder-width")).toBe("428");
  press("Home"); press("ArrowRight");
  expect(localStorage.getItem("agent-harness-builder-width")).toBe("340");
  press("End"); press("ArrowLeft");
  expect(localStorage.getItem("agent-harness-builder-width")).toBe("680");
});
