// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { FeishuConnection } from "../src/components/agent-studio/feishu-connection";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root; let host: HTMLDivElement;
const fetcher = vi.fn();
const connected = { configured: true, enabled: true, receiverOnline: true, receiverState: "awaiting_event", lastEventAt: null, bound: false, notifications: false, boundAt: null, pendingDeliveries: 0, failedDeliveries: 0 };
const configuration = { appId: "cli_test", configured: true, enabled: true, revision: 1,
  appliedRevision: 1, receiverLabel: "79 服务", canManage: true, source: "saved" };
beforeEach(() => {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  fetcher.mockReset(); fetcher.mockImplementation(async (url: string) => Response.json(url.endsWith("/connection") ? configuration : connected)); vi.stubGlobal("fetch", fetcher);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.unstubAllGlobals(); });
async function click(label: string) {
  const button = [...host.querySelectorAll("button")].find(b => b.textContent === label);
  expect(button).toBeTruthy(); await act(async () => button!.click());
}
it("does not describe a running receiver as a verified connection", async () => {
  fetcher.mockImplementation(async (url: string) => Response.json(url.endsWith("/connection") ? configuration : { ...connected, receiverState: "connecting" }));
  await act(async () => root.render(<FeishuConnection />));
  expect(host.textContent).toContain("连接中");
  expect(host.querySelector('[data-online="true"]')).toBeNull();
});
it("shows a one-time code only after authenticated creation succeeds", async () => {
  fetcher.mockImplementation(async (url: string) => Response.json(url.endsWith("/connection") ? configuration : url.endsWith("binding-code") ? { code: "ABCD1234", expiresAt: new Date(Date.now() + 600000).toISOString() } : connected));
  await act(async () => root.render(<FeishuConnection />));
  await click("生成绑定码");
  expect(host.querySelector<HTMLInputElement>("input")?.value).toBe("绑定 ABCD1234");
  expect(fetcher.mock.calls.find(([url]) => url.endsWith("binding-code"))?.[1].method).toBe("POST");
  expect([...host.querySelectorAll("button")].find(b => b.textContent === "重新生成绑定码")?.disabled).toBe(true);
});
it("keeps the binding visible when an unbind request fails", async () => {
  fetcher.mockImplementation(async (url: string) => url.endsWith("/connection") ? Response.json(configuration) : url.endsWith("/binding") ? new Response(null, { status: 503 }) : Response.json({ ...connected, bound: true, notifications: true }));
  await act(async () => root.render(<FeishuConnection />));
  await click("解除绑定"); await click("确认解除绑定");
  expect(host.textContent).toContain("你的飞书与 KAI 已关联");
  expect(host.querySelector('[role="alert"]')).not.toBeNull();
  expect(host.textContent).not.toContain("已解除绑定，后续通知");
});
it("uses server notification state and leaves the binding in place", async () => {
  let enabled = true;
  fetcher.mockImplementation(async (url: string, init: RequestInit) => {
    if (url.endsWith("/connection")) return Response.json(configuration);
    if (init.method === "PUT") { enabled = false; return new Response(null, { status: 204 }); }
    return Response.json({ ...connected, bound: true, notifications: enabled });
  });
  await act(async () => root.render(<FeishuConnection />)); await click("关闭飞书通知");
  expect(host.textContent).toContain("通知：已关闭");
  expect(host.textContent).toContain("你的飞书与 KAI 已关联");
});

it("saves a reconnect with a blank secret without resending the stored secret", async () => {
  await act(async () => root.render(<FeishuConnection />)); await click("配置连接");
  expect(host.querySelector('dialog[open]')).not.toBeNull();
  expect(host.querySelector<HTMLInputElement>('input[type="password"]')?.value).toBe("");
  await act(async () => { host.querySelector("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })); });
  const call = fetcher.mock.calls.find(([, init]) => init.method === "PUT");
  expect(JSON.parse(call![1].body)).toEqual({ expectedRevision: 1, appId: "cli_test" });
  expect(host.querySelector('dialog[open]')).toBeNull();
  expect(host.textContent).toContain("账号绑定和聊天记录已保留");
});

it("shows member binding controls without app credential fields", async () => {
  fetcher.mockImplementation(async (url: string) => Response.json(url.endsWith("/connection") ? { ...configuration, canManage: false } : connected));
  await act(async () => root.render(<FeishuConnection />));
  expect(host.textContent).toContain("生成绑定码");
  expect(host.querySelector('input[type="password"]')).toBeNull();
  expect([...host.querySelectorAll("button")].some(b => b.textContent === "配置连接")).toBe(false);
});

it("shows evidence-based diagnostics and waits for an actual test delivery", async () => {
  fetcher.mockImplementation(async (url: string) => {
    if (url.endsWith("/connection")) return Response.json(configuration);
    if (url.endsWith("/test")) return Response.json({ requestId: "connection-test:one", status: "pending" });
    if (url.includes("/tests/")) return Response.json({ status: "sent", messageId: "real-message" });
    if (url.endsWith("/diagnose")) return Response.json({ checkedAt: new Date().toISOString(), checks: [
      { key: "credentials", label: "应用凭据", status: "pass", detail: "凭据验证成功" },
      { key: "events", label: "消息接收", status: "pending", detail: "请发一句消息完成验证" },
    ] });
    return Response.json({ ...connected, bound: true });
  });
  await act(async () => root.render(<FeishuConnection />)); await click("发送测试消息");
  expect(host.textContent).toContain("测试消息已送达你的飞书");
  expect(host.querySelector('[data-state="pending"]')?.textContent).toContain("消息接收");
  expect(fetcher.mock.calls.some(([url]) => url.includes("/tests/"))).toBe(true);
});
