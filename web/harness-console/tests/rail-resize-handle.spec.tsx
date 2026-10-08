// @vitest-environment jsdom
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { RailResizeHandle } from "../src/components/rail-resize-handle";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let host: HTMLDivElement;
let root: Root;
let right: number;
let left: number;
const property = "--preferred-rail-width";
const key = "agent-harness-rail-width";

function Harness() {
  const [open, setOpen] = useState(true);
  const [expanded, setExpanded] = useState(false);
  return <main className={`console-shell${expanded ? " is-rail-expanded" : ""}`}>
    <button onClick={() => setOpen(true)}>打开</button>
    <div className="workspace-stage">
      <section className="task-content-shell" />
      <aside className="workbench-rail">{open && <RailResizeHandle expanded={expanded}
        onExpandedChange={setExpanded} onClose={() => setOpen(false)} />}</aside>
    </div>
  </main>;
}
beforeEach(() => {
  right = 1440;
  left = 264;
  const stored = new Map<string, string>();
  vi.stubGlobal("localStorage", { getItem: (key: string) => stored.get(key) ?? null, setItem: (key: string, value: string) => stored.set(key, value) });
  localStorage.setItem(key, "300");
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    if (this.classList.contains("task-content-shell")) return { left } as DOMRect;
    const width = this.closest(".is-rail-expanded") ? right - left : Number.parseFloat(document.documentElement.style.getPropertyValue(property)) || 300;
    return { right, width } as DOMRect;
  });
  HTMLElement.prototype.setPointerCapture = vi.fn();
  HTMLElement.prototype.hasPointerCapture = vi.fn(() => true);
  HTMLElement.prototype.releasePointerCapture = vi.fn();
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
});
afterEach(() => {
  act(() => root.unmount()); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); document.documentElement.removeAttribute("style");
});
function render() { act(() => root.render(<Harness />)); }
function pointer(type: string, x: number, pointerId = 1) {
  const event = new MouseEvent(type, { bubbles: true, clientX: x, button: 0 });
  Object.defineProperties(event, { pointerId: { value: pointerId }, isPrimary: { value: true } });
  act(() => host.querySelector('[role="separator"]')!.dispatchEvent(event));
}
function press(key: string) { act(() => host.querySelector('[role="separator"]')!.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }))); }
const shell = () => host.querySelector(".console-shell")!;
const expanded = () => shell().classList.contains("is-rail-expanded");
const saved = () => localStorage.getItem(key);

it("snaps the drag preview over the entire conversation before it becomes too narrow, without saving the expanded width", () => {
  localStorage.setItem(key, "420"); render();
  pointer("pointerdown", 1020); pointer("pointermove", 300);
  expect((shell() as HTMLElement).style.getPropertyValue("--drag-rail-width")).toBe("1176px");
  expect(shell().getAttribute("data-rail-resize-mode")).toBe("expanded");
  expect(expanded()).toBe(false); expect(saved()).toBe("420");
  pointer("pointerup", 300);
  expect(expanded()).toBe(true); expect(saved()).toBe("420");
  expect(shell().hasAttribute("data-rail-resizing")).toBe(false);
});
it("shrinks from the expanded view, remembers an ordinary width, and collapses on an inward drag", () => {
  render(); press("End"); expect(expanded()).toBe(true);
  pointer("pointerdown", 264); pointer("pointerup", 900);
  expect(expanded()).toBe(false); expect(saved()).toBe("540");
  pointer("pointerdown", 900); pointer("pointerup", 1280);
  expect(host.querySelector('[role="separator"]')).toBeNull(); expect(saved()).toBe("540");
  act(() => host.querySelector("button")!.click());
  expect(host.querySelector('[role="separator"]')!.getAttribute("aria-valuenow")).toBe("540");
  expect(expanded()).toBe(false);
});
it("cancels a drag without changing the saved width or expansion, including Escape and capture loss", () => {
  render(); press("End");
  for (const finish of ["pointercancel", "lostpointercapture", "Escape"]) {
    pointer("pointerdown", 264); pointer("pointermove", 1300);
    if (finish === "Escape") press(finish); else pointer(finish, 1300);
    expect(expanded()).toBe(true); expect(saved()).toBe("300");
    expect(shell().hasAttribute("data-rail-resizing")).toBe(false);
    expect((shell() as HTMLElement).style.getPropertyValue("--drag-rail-width")).toBe("");
  }
});
it("persists normal resizing, ignores a second pointer, and resets through the keyboard and double click", () => {
  render(); pointer("pointerdown", 1140); pointer("pointermove", 1100, 2); pointer("pointerup", 1100, 2);
  expect((shell() as HTMLElement).style.getPropertyValue("--drag-rail-width")).toBe("300px");
  pointer("pointerup", 800); expect(saved()).toBe("640");
  press("ArrowLeft"); expect(saved()).toBe("648");
  press("End"); expect(expanded()).toBe(true);
  act(() => host.querySelector('[role="separator"]')!.dispatchEvent(new MouseEvent("dblclick", { bubbles: true })));
  expect(expanded()).toBe(false); expect(saved()).toBe("300");
  press("Home"); expect(host.querySelector('[role="separator"]')).toBeNull();
});
it("cancels during viewport changes and cleans up if the handle unmounts while dragging", () => {
  render(); pointer("pointerdown", 1140); pointer("pointermove", 740);
  act(() => { right = 1100; window.dispatchEvent(new Event("resize")); });
  expect(shell().hasAttribute("data-rail-resizing")).toBe(false);
  expect(saved()).toBe("300");
  pointer("pointerdown", 800); pointer("pointermove", 600);
  const previousShell = shell(); act(() => root.render(null));
  expect(previousShell.hasAttribute("data-rail-resizing")).toBe(false);
});
it("keeps stored preferences when the viewport clamps them, and never previews an inward sliver", () => {
  localStorage.setItem(key, "600"); render();
  act(() => { right = 1100; window.dispatchEvent(new Event("resize")); });
  expect(host.querySelector('[role="separator"]')!.getAttribute("aria-valuenow")).toBe("356");
  expect(saved()).toBe("600");
  act(() => { right = 1440; window.dispatchEvent(new Event("resize")); });
  expect(host.querySelector('[role="separator"]')!.getAttribute("aria-valuenow")).toBe("600");
  pointer("pointerdown", 840); pointer("pointermove", 1300);
  expect(shell().getAttribute("data-rail-resize-mode")).toBe("collapsed");
  expect((shell() as HTMLElement).style.getPropertyValue("--drag-rail-width")).toBe("0px");
  pointer("pointercancel", 1300); expect(saved()).toBe("600");
});
it("expands the whole panel when a wide navigation leaves no room for both readable conversation and file preview", () => {
  vi.stubGlobal("innerWidth", 1100); right = 1100; left = 380;
  localStorage.setItem(key, "600"); render();
  expect(expanded()).toBe(true);
  expect(saved()).toBe("600");
  act(() => host.querySelector('[role="separator"]')!.dispatchEvent(new MouseEvent("dblclick", { bubbles: true })));
  expect(host.querySelector('[role="separator"]')).toBeNull();
  expect(saved()).toBe("600");
});
