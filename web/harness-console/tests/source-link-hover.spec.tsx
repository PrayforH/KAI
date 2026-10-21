// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
const mock = vi.hoisted(() => ({ copy: vi.fn() }));
vi.mock("../src/lib/clipboard", () => ({ writeTextToClipboard: mock.copy }));
vi.mock("../src/components/feedback-toast", () => ({ FeedbackToast: ({ message, tone }: { message: string; tone: string }) => message ? <span role={tone === "error" ? "alert" : "status"}>{message}</span> : null }));
import { SourceLink } from "../src/components/source-link";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let host: HTMLDivElement, root: Root;
const href = "https://docs.example.com/report?q=2026#summary";
const toolbar = () => document.querySelector<HTMLElement>('[role="toolbar"][aria-label="链接操作"]');
const anchor = () => host.querySelector<HTMLAnchorElement>("a")!;
async function enter(element: Element) { await act(async () => element.dispatchEvent(new MouseEvent("pointerover", { bubbles: true }))); }
async function leave(element: Element) { await act(async () => element.dispatchEvent(new MouseEvent("pointerout", { bubbles: true, relatedTarget: document.body }))); }
async function advance(ms: number) { await act(async () => vi.advanceTimersByTime(ms)); }
beforeEach(async () => { vi.useFakeTimers(); vi.clearAllMocks(); mock.copy.mockResolvedValue(true); host = document.createElement("div"); document.body.append(host); root = createRoot(host); await act(async () => root.render(<SourceLink href={href}>项目报告</SourceLink>)); });
afterEach(() => { act(() => root.unmount()); host.remove(); vi.useRealTimers(); });
it("waits before opening and ignores pointers that only pass across a link", async () => {
  await enter(anchor()); expect(toolbar()).toBeNull();
  await leave(anchor()); await advance(350); expect(toolbar()).toBeNull();
  await enter(anchor()); await advance(160);
  expect(toolbar()?.textContent).toBe("打开链接复制链接");
  expect(toolbar()?.querySelector("a")?.href).toBe(href);
  expect(toolbar()?.querySelector("a")?.rel).toBe("noopener noreferrer");
});
it("stays usable when moving from the link into its floating toolbar", async () => {
  await enter(anchor()); await advance(160); const panel = toolbar()!;
  await leave(anchor()); await advance(80); await enter(panel); await advance(250);
  expect(toolbar()).toBe(panel);
  await leave(panel); await advance(190); expect(toolbar()).toBeNull();
});
it("supports keyboard entry, arrow navigation and Escape without reopening", async () => {
  await act(async () => anchor().focus()); expect(toolbar()).not.toBeNull();
  await act(async () => anchor().dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true })));
  expect(document.activeElement).toBe(toolbar()?.querySelector("a"));
  await act(async () => toolbar()!.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowRight", bubbles: true })));
  expect(document.activeElement).toBe(toolbar()?.querySelector("button"));
  await act(async () => document.activeElement!.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
  await advance(500); expect(toolbar()).toBeNull(); expect(document.activeElement).toBe(anchor());
});
it("copies the exact destination only once while copying is pending", async () => {
  let finish!: (value: boolean) => void;
  mock.copy.mockReturnValue(new Promise(resolve => { finish = resolve; }));
  await enter(anchor()); await advance(160); const copy = toolbar()!.querySelector("button")!;
  await act(async () => { copy.click(); copy.click(); });
  expect(mock.copy).toHaveBeenCalledExactlyOnceWith(href);
  await act(async () => finish(true));
  expect(toolbar()).toBeNull(); expect(host.textContent).toContain("链接已复制");
});
it("keeps the actions available after a copy failure for a retry", async () => {
  mock.copy.mockResolvedValueOnce(false).mockResolvedValueOnce(true);
  await enter(anchor()); await advance(160);
  await act(async () => toolbar()!.querySelector("button")!.click());
  expect(host.querySelector('[role="alert"]')?.textContent).toBe("复制失败，请重试。");
  expect(toolbar()).not.toBeNull();
  await act(async () => toolbar()!.querySelector("button")!.click()); expect(toolbar()).toBeNull();
});
it("dismisses for another link, scrolling and changing streaming destinations", async () => {
  await enter(anchor()); await advance(160);
  await act(async () => document.dispatchEvent(new CustomEvent("harness:link-hover-open", { detail: "other-link" })));
  expect(toolbar()).toBeNull();
  await enter(anchor()); await advance(160); await act(async () => window.dispatchEvent(new Event("scroll"))); expect(toolbar()).toBeNull();
  await enter(anchor()); await advance(160);
  await act(async () => root.render(<SourceLink href="https://docs.example.com/other">另一报告</SourceLink>)); expect(toolbar()).toBeNull();
});
