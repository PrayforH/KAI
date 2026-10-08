// @vitest-environment jsdom
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { RailResizeHandle } from "../src/components/rail-resize-handle";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let host: HTMLDivElement;
let root: Root;
let right: number;
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
  const stored = new Map<string, string>();
  vi.stubGlobal("localStorage", { getItem: (key: string) => stored.get(key) ?? null, setItem: (key: string, value: string) => stored.set(key, value) });
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    if (this.classList.contains("task-content-shell")) return { left: 264 } as DOMRect;
    const width = this.closest(".is-rail-expanded") ? right - 264 : Number.parseFloat(document.documentElement.style.getPropertyValue(property)) || 300;
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

it("follows the pointer beyond half the viewport, and snaps over the conversation on release without saving the expanded width", () => {
  localStorage.setItem(key, "420"); render();
  pointer("pointerdown", 1020); pointer("pointermove", 300);
  expect((shell() as HTMLElement).style.getPropertyValue("--drag-rail-width")).toBe("1140px");
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
  pointer("pointerup", 740); expect(saved()).toBe("700");
  press("ArrowLeft"); expect(saved()).toBe("708");
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
