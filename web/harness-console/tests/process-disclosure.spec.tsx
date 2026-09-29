// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ProcessDisclosure } from "../src/components/process-disclosure";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let host: HTMLDivElement, root: Root;
let reduced = false;
let animations: Array<{ cancel: ReturnType<typeof vi.fn>; onfinish: (() => void) | null }>;
beforeEach(() => {
  animations = []; reduced = false;
  vi.stubGlobal("matchMedia", () => ({ matches: reduced }));
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
    return { height: this.hidden || this.style.height === "0px" ? 0 : 100 } as DOMRect;
  });
  vi.stubGlobal("Animation", class {});
  Object.defineProperty(HTMLElement.prototype, "animate", { configurable: true, value: vi.fn(() => {
    const animation = { cancel: vi.fn(), onfinish: null }; animations.push(animation); return animation;
  }) });
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals(); delete (HTMLElement.prototype as unknown as { animate?: unknown }).animate; });
const render = (open: boolean, text = "内容") => act(() => root.render(<ProcessDisclosure open={open}>{text}</ProcessDisclosure>));
it("keeps closing content mounted but inaccessible until the transition ends", () => {
  render(true); render(false);
  const node = host.firstElementChild as HTMLElement;
  expect(node.hidden).toBe(false); expect(node.getAttribute("aria-hidden")).toBe("true");
  expect(node.hasAttribute("inert")).toBe(true); expect(animations).toHaveLength(1);
  animations[0].onfinish?.(); expect(node.hidden).toBe(true);
});
it("does not restart animation on tokens and cancels it when the user reopens", () => {
  render(true); render(true, "新片段"); expect(animations).toHaveLength(0);
  render(false); render(true); expect(animations[0].cancel).toHaveBeenCalled();
  expect((host.firstElementChild as HTMLElement).hidden).toBe(false);
  expect(host.firstElementChild?.getAttribute("aria-hidden")).toBe("false");
});
it("shows history immediately and respects reduced motion", () => {
  reduced = true; render(false); expect((host.firstElementChild as HTMLElement).hidden).toBe(true);
  render(true); expect((host.firstElementChild as HTMLElement).hidden).toBe(false);
  render(false); expect((host.firstElementChild as HTMLElement).hidden).toBe(true);
  expect(animations).toHaveLength(0);
});
