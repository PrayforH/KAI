// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { Specialist } from "../src/lib/assistant-client";
const request = vi.hoisted(() => vi.fn());
vi.mock("../src/lib/assistant-client", () => ({assistantRequest: (...args: unknown[]) => request(...args)}));
vi.mock("next/navigation", () => ({useRouter: () => ({push: vi.fn()})}));
vi.mock("next/link", () => ({default: ({children, scroll, ...props}: {children: React.ReactNode; scroll?: boolean}) => <a {...props}>{children}</a>}));
import { BotRail } from "../src/components/bot-rail";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
const bot = (n: number, extra = {}) => ({specialistId: `bot-${n}`, name: `Bot ${n}`, status: "ready", config: {},
  createdAt: `2026-11-01T00:00:0${n}Z`, updatedAt: "2026-11-02T00:00:00Z", ...extra}) as Specialist;
let host: HTMLDivElement, root: Root;
beforeEach(() => {vi.clearAllMocks(); host = document.createElement("div"); document.body.append(host); root = createRoot(host);});
afterEach(async () => {await act(async () => root.unmount()); host.remove();});
const names = () => [...host.querySelectorAll(".bot-rail-label strong")].map(n => n.textContent);
async function mount() {await act(async () => root.render(<BotRail currentThreadId="kai-personal-assistant-v1" />));}
it("does not jump after polling changes read times, statuses or source response ordering", async () => {
  request.mockResolvedValue([bot(3), bot(1), bot(2)]); await mount();
  expect(names()).toEqual(["主助理", "Bot 1", "Bot 2", "Bot 3"]);
  request.mockResolvedValue([bot(2, {status: "active", updatedAt: "2099-01-01T00:00:00Z"}), bot(3), bot(1, {status: "needs_input"})]);
  await act(async () => window.dispatchEvent(new CustomEvent("kai:specialists-changed")));
  expect(names()).toEqual(["主助理", "Bot 1", "Bot 2", "Bot 3"]);
  expect(host.textContent).not.toContain("需要关注");
});
it("collapses the whole directory and reveals all Bot groups on request", async () => {
  request.mockResolvedValue([bot(7), bot(1), bot(2), bot(3), bot(4), bot(5), bot(6)]); await mount();
  expect(names()).toHaveLength(6);
  const more = host.querySelector<HTMLButtonElement>('button[aria-label="展开机器人"]')!;
  expect(more.className).toBe("task-list-item tasks-show-more");
  expect(more.querySelector('.task-list-title')?.textContent).toBe('展开显示（2）');
  expect(host.querySelector('.task-list-toolbar .task-list-heading .task-section-toggle')).not.toBeNull();
  await act(async () => more.click()); expect(names()).toHaveLength(8);
  const toggle = host.querySelector<HTMLButtonElement>('[aria-label="收起机器人分组"]')!;
  await act(async () => toggle.click());
  expect(host.querySelector('[id][hidden]')).not.toBeNull();
  expect(host.querySelector('[aria-label="展开机器人分组"]')).not.toBeNull();
});
it("main contact has the same menu and stored appearance without another main conversation", async () => {
  request.mockResolvedValue([bot(1), bot(0, {specialistId: "specialist_primary_abc", name: "我的总管", config: {sidebar: {pinned: true}, appearance: {avatar: "leaf", color: "#0d9381"}}})]);
  await mount();
  expect(names()).toEqual(["我的总管", "Bot 1"]);
  expect(host.querySelector('a[href="/assistant"] [data-shape="leaf"]')).not.toBeNull();
  expect(host.querySelector('button[aria-label="我的总管 的更多操作"]')).not.toBeNull();
  expect(host.querySelectorAll('a[href="/assistant"]')).toHaveLength(1);
});
