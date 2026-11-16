// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MessageActions } from "../src/components/message-actions";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | undefined;
let host: HTMLDivElement;

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
});

afterEach(() => {
  act(() => root?.unmount());
  root = undefined;
  host.remove();
  vi.restoreAllMocks();
});

async function renderInScroller() {
  const scroller = document.createElement("div");
  scroller.style.overflowY = "auto";
  scroller.dataset.testid = "scroller";
  host.appendChild(scroller);
  const mount = document.createElement("div");
  scroller.appendChild(mount);
  root = createRoot(mount);
  await act(async () =>
    root!.render(
      <MessageActions reactions={[]} onReact={() => undefined} copyText="hi" />,
    ),
  );
  return scroller;
}

function picker(): HTMLElement | null {
  return host.querySelector('[role="group"][aria-label="选择表情"]');
}

describe("message actions emoji picker", () => {
  it("never renders a single-message delete action", async () => {
    await renderInScroller();
    expect(host.querySelector('[aria-label="删除这条消息"]')).toBeNull();
    expect(host.querySelector('[aria-label="确认删除这条消息"]')).toBeNull();
  });

  it("opening the picker does not shift the action bar", async () => {
    await renderInScroller();
    const button = host.querySelector<HTMLButtonElement>('[aria-label="给这条消息加表情"]')!;
    await act(async () => button.click());
    expect(picker()).not.toBeNull();
    expect(button.getAttribute("aria-expanded")).toBe("true");
  });

  it("flips below the action bar when the message sits at the clipped top edge", async () => {
    const scroller = await renderInScroller();
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
      function (this: HTMLElement) {
        // 弹层向上展开时顶到滚动区上沿之前：40 < 100，会被裁掉半截。
        const top = this.getAttribute("role") === "group" ? 40 : this === scroller ? 100 : 0;
        return { top, bottom: top, left: 0, right: 0, width: 0, height: 0, x: 0, y: 0, toJSON: () => ({}) } as DOMRect;
      },
    );
    const button = host.querySelector<HTMLButtonElement>('[aria-label="给这条消息加表情"]')!;
    await act(async () => button.click());
    await act(async () => undefined);
    expect(picker()?.className).toMatch(/reactionPickerBelow/);
  });

  it("keeps the picker above when there is room", async () => {
    const scroller = await renderInScroller();
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(
      function (this: HTMLElement) {
        const top = this.getAttribute("role") === "group" ? 140 : this === scroller ? 100 : 0;
        return { top, bottom: top, left: 0, right: 0, width: 0, height: 0, x: 0, y: 0, toJSON: () => ({}) } as DOMRect;
      },
    );
    const button = host.querySelector<HTMLButtonElement>('[aria-label="给这条消息加表情"]')!;
    await act(async () => button.click());
    await act(async () => undefined);
    expect(picker()?.className ?? "").not.toMatch(/reactionPickerBelow/);
  });
});
