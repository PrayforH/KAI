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
beforeEach(async () => { vi.useFakeTimers(); vi.clearAllMocks(); vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ preview: null }) })); mock.copy.mockResolvedValue(true); host = document.createElement("div"); document.body.append(host); root = createRoot(host); await act(async () => root.render(<SourceLink href={href}>项目报告</SourceLink>)); });
afterEach(() => { act(() => root.unmount()); host.remove(); vi.useRealTimers(); vi.unstubAllGlobals(); });
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

it("progressively loads website details after lingering, without replacing the anchor", async () => {
  let finish!: (value: unknown) => void;
  vi.mocked(fetch).mockReturnValue(new Promise(resolve => { finish = resolve as typeof finish; }));
  const original = anchor();
  await enter(original); await advance(160); expect(fetch).not.toHaveBeenCalled();
  await advance(60); expect(fetch).toHaveBeenCalledWith(`/api/link-preview?url=${encodeURIComponent(href)}`, expect.objectContaining({ credentials: "same-origin" }));
  await advance(550); expect(toolbar()?.textContent).toContain("docs.example.com");
  expect(toolbar()?.querySelector('[role="status"]')).not.toBeNull();
  await act(async () => finish({ ok: true, json: async () => ({ preview: { title: "项目标题", description: "公开页面摘要" } }) }));
  expect(toolbar()?.querySelector("h3")?.textContent).toBe("项目标题");
  expect(toolbar()?.querySelector("p")?.textContent).toBe("公开页面摘要");
  expect(anchor()).toBe(original);
});
it("keeps open and copy actions when preview fetch fails", async () => {
  vi.mocked(fetch).mockRejectedValue(new Error("offline"));
  await enter(anchor()); await advance(160); await advance(600);
  expect(toolbar()?.querySelector("h3")).toBeNull();
  expect(toolbar()?.querySelector("a")?.href).toBe(href);
  expect(toolbar()?.querySelector('button[aria-label="复制链接"]')).not.toBeNull();
});
it("aborts dismissed previews and ignores late responses for a different link", async () => {
  let finish!: (value: unknown) => void;
  vi.mocked(fetch).mockReturnValue(new Promise(resolve => { finish = resolve as typeof finish; }));
  await enter(anchor()); await advance(160); await advance(60);
  const signal = vi.mocked(fetch).mock.calls[0][1]?.signal;
  await act(async () => root.render(<SourceLink href="https://example.org/other">另一链接</SourceLink>));
  expect(signal?.aborted).toBe(true);
  await act(async () => finish({ ok: true, json: async () => ({ preview: { title: "过期标题", description: null } }) }));
  await enter(anchor()); await advance(160);
  expect(toolbar()?.textContent).not.toContain("过期标题");
});
