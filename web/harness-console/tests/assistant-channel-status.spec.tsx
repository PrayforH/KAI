// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AssistantChannelStatus } from "../src/components/assistant-channel-status";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root; let host: HTMLDivElement;
const fetcher = vi.fn();
beforeEach(() => {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  fetcher.mockReset(); vi.stubGlobal("fetch", fetcher);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.unstubAllGlobals(); });
it("shows only the owner's bound and online channels with a settings shortcut", async () => {
  fetcher.mockImplementation(async () => Response.json({ enabled: true, bound: true, online: true, receiverOnline: true }));
  await act(async () => root.render(<AssistantChannelStatus />));
  expect(host.textContent).toBe("已连接：微信飞书");
  expect(host.querySelector("a")?.getAttribute("href")).toBe("/settings#assistant");
  expect(fetcher.mock.calls.every(([, init]) => init.cache === "no-store")).toBe(true);
});
it("does not treat an online shared receiver as a personal account binding", async () => {
  fetcher.mockImplementation(async () => Response.json({ enabled: true, bound: false, online: true, receiverOnline: true }));
  await act(async () => root.render(<AssistantChannelStatus />));
  expect(host.textContent).toContain("微信待绑定"); expect(host.textContent).toContain("飞书待绑定");
  expect(host.textContent).not.toContain("已连接");
});
it("replaces previously connected state with an honest unknown state on refresh failure", async () => {
  fetcher.mockImplementation(async () => Response.json({ enabled: true, bound: true, online: true, receiverOnline: true }));
  await act(async () => root.render(<AssistantChannelStatus />));
  fetcher.mockRejectedValue(new Error("network"));
  await act(async () => window.dispatchEvent(new Event("focus")));
  expect(host.textContent).toContain("微信状态待确认"); expect(host.textContent).not.toContain("已连接");
});
it("distinguishes a disconnected channel from a bound offline receiver", async () => {
  fetcher.mockImplementation(async (url: string) => Response.json(url.includes("/weixin/") ? { enabled: false } : { enabled: true, bound: true, receiverOnline: false }));
  await act(async () => root.render(<AssistantChannelStatus />));
  expect(host.textContent).toContain("飞书离线"); expect(host.textContent).not.toContain("已连接");
  expect(host.textContent).not.toContain("微信");
});
