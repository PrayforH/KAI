// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AssistantBotSettings } from "../src/components/assistant-bot-settings";
import type { Specialist } from "../src/lib/assistant-client";

const mock = vi.hoisted(() => ({ request: vi.fn(), confirm: vi.fn() }));
vi.mock("../src/lib/assistant-client", () => ({ assistantRequest: mock.request }));
vi.mock("../src/components/confirmation-dialog", () => ({ useConfirmationDialog: () => ({ requestConfirmation: mock.confirm, confirmationDialog: null }) }));
vi.mock("../src/components/bot-rail", () => ({ BotRailAvatar: () => <span />, specialistAppearance: () => ({ color: "#123456", avatar: "blob" }) }));
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root, host: HTMLDivElement;
let bots: Specialist[];
beforeEach(() => {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  bots = [
    { specialistId: "archived", name: "合成归档", status: "archived", revision: 3, config: {sidebar: {hidden: true}} },
    { specialistId: "hidden", name: "合成隐藏", status: "paused", revision: 2, config: {sidebar: {hidden: true}} },
    { specialistId: "visible", name: "可用机器人", status: "active", revision: 1, config: {} },
  ] as Specialist[];
  mock.request.mockReset(); mock.confirm.mockReset().mockResolvedValue(true);
  mock.request.mockImplementation(async (path: string, method = "GET", body?: {status?: Specialist["status"];sidebar?: unknown}) => {
    if (path === "specialists") return bots;
    const id = path.split("/")[1];
    const bot = bots.find(item => item.specialistId === id)!;
    if (method === "DELETE") { bots = bots.filter(item => item !== bot); return; }
    if (path.endsWith("/status")) { const next = {...bot, status: body!.status, revision: bot.revision + 1}; bots = bots.map(item => item === bot ? next as Specialist : item); return next; }
    if (path.endsWith("/profile")) { bots = bots.map(item => item === bot ? {...bot, config: {sidebar: body!.sidebar}} as Specialist : item); return; }
    return { specialist: bot };
  });
});
afterEach(() => { act(() => root.unmount()); host.remove(); });
async function mount() { await act(async () => root.render(<AssistantBotSettings />)); }
async function click(label: string) { await act(async () => (host.querySelector(`[aria-label="${label}"]`) as HTMLButtonElement).click()); }

it("keeps archived and hidden contacts in settings, and restores without starting work", async () => {
  await mount();
  expect(host.textContent).not.toContain("可用机器人");
  await click("恢复 合成归档");
  expect(mock.request).toHaveBeenCalledWith("specialists/archived/status", "POST", {status: "paused", expectedRevision: 3});
  expect(mock.request).toHaveBeenCalledWith("specialists/archived/profile", "POST", expect.objectContaining({ expectedRevision: 4, sidebar: expect.objectContaining({hidden: false}) }));
  expect(host.textContent).not.toContain("合成归档");
  expect(mock.request.mock.calls.some(([path]) => /start|resume|executions/.test(path))).toBe(false);
});

it("does not delete when confirmation is cancelled, then deletes the exact confirmed identity", async () => {
  await mount(); mock.confirm.mockResolvedValueOnce(false);
  await click("删除 合成归档");
  expect(mock.request.mock.calls.some(([, method]) => method === "DELETE")).toBe(false);
  await click("删除 合成归档");
  expect(mock.request).toHaveBeenCalledWith("specialists/archived", "DELETE", {expectedRevision: 3, confirmName: "合成归档"});
  expect(mock.confirm).toHaveBeenCalledWith(expect.objectContaining({description: expect.stringContaining("已有聊天") }));
});
