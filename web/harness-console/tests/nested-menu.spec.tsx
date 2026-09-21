// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { NestedMenu } from "../src/components/nested-menu";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

describe("nested menu", () => {
  let container: HTMLDivElement;
  let root: Root;
  const parentKeyDown = vi.fn();
  const trigger = () => container.querySelector<HTMLButtonElement>(".nested-menu-trigger")!;
  const options = () => container.querySelectorAll<HTMLButtonElement>(".nested-menu-panel button");
  const key = (target: HTMLElement, value: string) => act(() => {
    target.dispatchEvent(new KeyboardEvent("keydown", { key: value, bubbles: true }));
  });

  beforeEach(() => {
    vi.useFakeTimers();
    parentKeyDown.mockClear();
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => setTimeout(() => callback(0), 0));
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => root.render(<div onKeyDown={parentKeyDown}>
      <NestedMenu label="主题外观" icon={<span />}>
        <button>深色</button><button disabled>不可用</button><button>浅色</button>
      </NestedMenu>
      <button className="outside">外部</button>
    </div>));
  });
  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it("supports keyboard navigation and closes only the submenu on Escape", () => {
    act(() => trigger().focus());
    key(trigger(), "ArrowRight");
    act(() => vi.runAllTimers());
    expect(document.activeElement).toBe(options()[0]);
    key(options()[0], "ArrowDown");
    expect(document.activeElement).toBe(options()[2]);
    parentKeyDown.mockClear();
    key(options()[2], "Escape");
    expect(options()).toHaveLength(0);
    expect(document.activeElement).toBe(trigger());
    expect(parentKeyDown).not.toHaveBeenCalled();
  });

  it("opens on hover, allows travel into the panel, and closes after leaving", () => {
    act(() => trigger().dispatchEvent(new MouseEvent("mouseover", { bubbles: true })));
    expect(options()).toHaveLength(3);
    const outside = container.querySelector<HTMLButtonElement>(".outside")!;
    act(() => trigger().dispatchEvent(new MouseEvent("mouseout", { bubbles: true, relatedTarget: outside })));
    act(() => vi.advanceTimersByTime(100));
    expect(options()).toHaveLength(3);
    act(() => outside.dispatchEvent(new MouseEvent("mouseout", { bubbles: true, relatedTarget: options()[0] })));
    act(() => vi.advanceTimersByTime(200));
    expect(options()).toHaveLength(3);
    act(() => options()[0].dispatchEvent(new MouseEvent("mouseout", { bubbles: true, relatedTarget: outside })));
    act(() => vi.advanceTimersByTime(200));
    expect(options()).toHaveLength(0);
  });
});
