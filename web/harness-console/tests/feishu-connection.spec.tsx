// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { FeishuConnection } from "../src/components/agent-studio/feishu-connection";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root; let host: HTMLDivElement;
const fetcher = vi.fn();
const connected = { configured: true, enabled: true, receiverOnline: true, receiverState: "awaiting_event", lastEventAt: null, bound: false, notifications: false, boundAt: null, pendingDeliveries: 0, failedDeliveries: 0 };
beforeEach(() => {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  fetcher.mockReset(); fetcher.mockImplementation(async () => Response.json(connected)); vi.stubGlobal("fetch", fetcher);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.unstubAllGlobals(); });
async function click(label: string) {
  const button = [...host.querySelectorAll("button")].find(b => b.textContent === label);
  expect(button).toBeTruthy(); await act(async () => button!.click());
}
it("does not describe a running receiver as a verified connection", async () => {
  await act(async () => root.render(<FeishuConnection />));
  expect(host.textContent).toContain("等待消息验证");
  expect(host.textContent).not.toContain("已收到飞书事件");
});
it("shows a one-time code only after authenticated creation succeeds", async () => {
  fetcher.mockImplementation(async (url: string) => Response.json(url.endsWith("binding-code") ? { code: "ABCD1234", expiresAt: new Date(Date.now() + 600000).toISOString() } : connected));
  await act(async () => root.render(<FeishuConnection />));
  await click("生成绑定码");
  expect(host.querySelector<HTMLInputElement>("input")?.value).toBe("绑定 ABCD1234");
  expect(fetcher.mock.calls.find(([url]) => url.endsWith("binding-code"))?.[1].method).toBe("POST");
  expect([...host.querySelectorAll("button")].find(b => b.textContent === "重新生成绑定码")?.disabled).toBe(true);
});
it("keeps the binding visible when an unbind request fails", async () => {
  fetcher.mockImplementation(async (url: string) => url.endsWith("/binding") ? new Response(null, { status: 503 }) : Response.json({ ...connected, bound: true, notifications: true }));
  await act(async () => root.render(<FeishuConnection />));
  await click("解除绑定"); await click("确认解除绑定");
  expect(host.textContent).toContain("你的账号已绑定");
  expect(host.querySelector('[role="alert"]')).not.toBeNull();
  expect(host.textContent).not.toContain("已解除绑定，后续通知");
});
it("uses server notification state and leaves the binding in place", async () => {
  let enabled = true;
  fetcher.mockImplementation(async (_: string, init: RequestInit) => {
    if (init.method === "PUT") { enabled = false; return new Response(null, { status: 204 }); }
    return Response.json({ ...connected, bound: true, notifications: enabled });
  });
  await act(async () => root.render(<FeishuConnection />)); await click("关闭飞书通知");
  expect(host.textContent).toContain("通知：已关闭");
  expect(host.textContent).toContain("你的账号已绑定");
});
