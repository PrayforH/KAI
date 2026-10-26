// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AssistantManager } from "../src/components/agent-studio/assistant-manager";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
const item = { objectiveId: "o1", title: "采购整改跟进", instructions: "核对补充材料", agentName: "audit", agentVersion: "1", projectId: null, status: "active", revision: 7, schedule: null, nextCheckAt: null, completionCriteria: "证据齐全", endsAt: null, notificationMode: "none" };
let root: Root; let host: HTMLDivElement;
const fetcher = vi.fn();
beforeEach(() => {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  fetcher.mockReset();
  fetcher.mockImplementation(async (url: string) => Response.json(url.endsWith("/objectives") ? [item] : []));
  vi.stubGlobal("fetch", fetcher);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.unstubAllGlobals(); });
async function click(label: string) {
  const button = [...host.querySelectorAll("button")].find(b => b.textContent === label);
  expect(button).toBeTruthy(); await act(async () => button!.click());
}
it("uses the selected objective revision when pausing", async () => {
  await act(async () => root.render(<AssistantManager />));
  await act(async () => host.querySelector<HTMLButtonElement>("button[data-selected]")!.click());
  await click("暂停");
  const request = fetcher.mock.calls.find(([url]) => url.endsWith("/actions"));
  expect(JSON.parse(request![1].body)).toEqual({ action: "pause", expectedRevision: 7 });
});
it("keeps existing objectives accessible without a duplicate channel configuration", async () => {
  await act(async () => root.render(<AssistantManager />));
  expect(host.textContent).toContain("采购整改跟进");
  expect(host.textContent).not.toContain("飞书连接");
  expect(fetcher.mock.calls.some(([url]) => url.includes("/feishu/"))).toBe(false);
  expect(fetcher.mock.calls.every(([, init]) => init.method === "GET")).toBe(true);
});
it("retains a visible error instead of claiming a stale action succeeded", async () => {
  await act(async () => root.render(<AssistantManager />));
  await act(async () => host.querySelector<HTMLButtonElement>("button[data-selected]")!.click());
  fetcher.mockImplementation(async (url: string) => url.endsWith("/actions") ? new Response(null, { status: 409 }) : Response.json([]));
  await click("停止事项");
  expect(host.querySelector('[role="alert"]')?.textContent).toContain("事项已更新或已有执行");
  expect(host.querySelector('[role="status"]')).toBeNull();
});
