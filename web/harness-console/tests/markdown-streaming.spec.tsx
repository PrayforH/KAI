// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { TextMessagePartProvider } from "@assistant-ui/react";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { MarkdownText } from "../src/components/markdown-text";
vi.mock("../src/components/mermaid-diagram", () => ({ MermaidCodeHeader: () => null, MermaidDiagram: () => null }));
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let host: HTMLDivElement;
let root: Root;
let reducedMotion = false;
beforeEach(() => {
  vi.useFakeTimers();
  reducedMotion = false;
  vi.stubGlobal("matchMedia", vi.fn(() => ({ matches: reducedMotion, addEventListener: vi.fn(), removeEventListener: vi.fn() })));
  vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => setTimeout(() => callback(Date.now()), 16));
  vi.stubGlobal("cancelAnimationFrame", (id: ReturnType<typeof setTimeout>) => clearTimeout(id));
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); vi.useRealTimers(); vi.unstubAllGlobals(); });
function render(text: string, running = true, id = "answer") {
  act(() => root.render(<TextMessagePartProvider key={id} text={text} isRunning={running}><MarkdownText /></TextMessagePartProvider>));
}
function advance(ms: number) { act(() => vi.advanceTimersByTime(ms)); }
it("spreads a chunk over multiple paints and drains the exact final answer", () => {
  render("");
  const answer = "春风吹过山谷，溪水流向远方。".repeat(10);
  render(answer);
  advance(48);
  const first = host.textContent!;
  expect(first.length).toBeGreaterThan(0);
  expect(first.length).toBeLessThan(answer.length);
  advance(48);
  expect(host.textContent!.length).toBeGreaterThan(first.length);
  render(answer, false);
  advance(1000);
  expect(host.textContent).toBe(answer);
});
it("renders incomplete bold while streaming and preserves literal text at completion", () => {
  render("这是 **重点内容");
  advance(300);
  expect(host.querySelector("strong")?.textContent).toBe("重点内容");
  render("这是 **重点内容", false);
  advance(300);
  expect(host.querySelector("strong")).toBeNull();
  expect(host.textContent).toBe("这是 **重点内容");
});
it("shows history immediately and does not replay a previous thread", () => {
  render("历史内容", false);
  expect(host.textContent).toBe("历史内容");
  render("旧会话".repeat(100), true);
  advance(64);
  render("新会话", true, "new");
  expect(host.textContent).not.toContain("旧会话");
  advance(200);
  expect(host.textContent).toBe("新会话");
});
it("respects reduced motion and does not create a slow animation backlog", () => {
  reducedMotion = true;
  render("即时显示".repeat(300));
  expect(host.textContent).toBe("即时显示".repeat(300));
});
it("keeps completed links, tables and code functional", () => {
  render("[来源](https://example.com/report)\n\n| 项 | 值 |\n| --- | --- |\n| 甲 | 一 |\n\n```js\nconst x = 1;\n```", false);
  expect(host.querySelector("a")?.getAttribute("href")).toBe("https://example.com/report");
  expect(host.querySelector("table")?.textContent).toContain("甲");
  expect(host.querySelector("code")?.textContent).toContain("const x = 1;");
});
it("drains a large final chunk promptly and does not leave timers after unmount", () => {
  render("");
  const answer = "完整结果".repeat(1500);
  render(answer);
  advance(32);
  render(answer, false);
  for (let i = 0; i < 20; i++) advance(16);
  expect(host.textContent).toBe(answer);
  act(() => root.render(null));
  expect(vi.getTimerCount()).toBe(0);
});
it("does not make an incomplete URL clickable", () => {
  render("[来源](https://example");
  advance(300);
  expect(host.textContent).toBe("来源");
  expect(host.querySelector("a")).toBeNull();
});
