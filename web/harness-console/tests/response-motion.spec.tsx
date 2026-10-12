// @vitest-environment jsdom
import { act, useRef, useState, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useResponseMotion } from "../src/components/use-response-motion";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root | undefined;
let host: HTMLDivElement;
const animateDescriptor = Object.getOwnPropertyDescriptor(Element.prototype, "animate");
const mediaDescriptor = Object.getOwnPropertyDescriptor(window, "matchMedia");
let reduced = false;
let preferenceChanged: (() => void) | undefined;
let animations: { cancel: ReturnType<typeof vi.fn>; onfinish: unknown; oncancel: unknown }[];
const animate = vi.fn(() => {
  const animation = { cancel: vi.fn(), onfinish: null, oncancel: null };
  animations.push(animation); return animation;
});
beforeEach(() => {
  animations = []; reduced = false; preferenceChanged = undefined; animate.mockClear();
  Object.defineProperty(Element.prototype, "animate", { configurable: true, value: animate });
  Object.defineProperty(window, "matchMedia", { configurable: true, value: () => ({
    get matches() { return reduced; },
    addEventListener: (_name: string, callback: () => void) => { preferenceChanged = callback; },
    removeEventListener: vi.fn(),
  }) });
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
});
afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined; host.remove();
  if (animateDescriptor) Object.defineProperty(Element.prototype, "animate", animateDescriptor);
  else Reflect.deleteProperty(Element.prototype, "animate");
  if (mediaDescriptor) Object.defineProperty(window, "matchMedia", mediaDescriptor);
  else Reflect.deleteProperty(window, "matchMedia");
  document.getSelection()?.removeAllRanges();
});
function Response({ text = "已有内容", streaming = false, extra }: { text?: string; streaming?: boolean; extra?: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null); useResponseMotion(ref, true);
  return <div ref={ref} data-streaming={String(streaming)}><div className="aui-md"><p>{text}</p>{extra}</div></div>;
}
async function render(node: ReactNode) { await act(async () => root!.render(node)); }
it("keeps history and existing text steady, revealing only new blocks", async () => {
  await render(<Response streaming />); const paragraph = host.querySelector("p");
  expect(animate).not.toHaveBeenCalled();
  await render(<Response text="已有内容，继续增长" streaming />);
  expect(host.querySelector("p")).toBe(paragraph); expect(animate).not.toHaveBeenCalled();
  await render(<Response text="已有内容，继续增长" streaming extra={<p>新增段落</p>} />);
  expect(animate).toHaveBeenCalledTimes(1);
  await render(<Response text="已有内容，继续增长" extra={<p>新增段落，完成</p>} />);
  expect(animate).toHaveBeenCalledTimes(1);
});
it("does not animate nested children twice and reveals appended list rows", async () => {
  await render(<Response streaming />);
  await render(<Response streaming extra={<ul><li>一</li><li>二</li></ul>} />);
  expect(animate).toHaveBeenCalledTimes(1); expect(animate.mock.contexts[0]).toBe(host.querySelector("ul"));
  await render(<Response streaming extra={<ul><li>一</li><li>二</li><li>三</li></ul>} />);
  expect(animate).toHaveBeenCalledTimes(2); expect(animate.mock.contexts[1]).toBe(host.querySelector("li:last-child"));
});
it("reveals user-expanded history without animating initial history", async () => {
  function Folded() {
    const ref = useRef<HTMLDivElement>(null); const [open, setOpen] = useState(false); useResponseMotion(ref, true);
    return <div ref={ref} data-streaming="false"><div className="aui-md"><ul><li>已有</li>{open && <li>展开内容</li>}</ul></div><div className="aui-md-clamp"><button onClick={() => setOpen(true)}>展开</button></div></div>;
  }
  await render(<Folded />); expect(animate).not.toHaveBeenCalled();
  await act(async () => host.querySelector<HTMLButtonElement>("button")!.click());
  expect(animate).toHaveBeenCalledTimes(1);
});
it("respects selection and cancels when the motion preference changes", async () => {
  await render(<Response streaming />);
  const range = document.createRange(); range.selectNodeContents(host.querySelector("p")!); document.getSelection()?.addRange(range);
  await render(<Response streaming extra={<p>阅读选中时新增</p>} />); expect(animate).not.toHaveBeenCalled();
  document.getSelection()?.removeAllRanges();
  await render(<Response streaming extra={<><p>阅读选中时新增</p><p>继续新增</p></>} />); expect(animate).toHaveBeenCalled();
  reduced = true; preferenceChanged?.();
  expect(animations.every(animation => animation.cancel.mock.calls.length === 1)).toBe(true);
  const count = animate.mock.calls.length;
  await render(<Response streaming extra={<p>减少动态效果时新增</p>} />); expect(animate).toHaveBeenCalledTimes(count);
  await act(async () => root!.unmount()); root = undefined;
});
