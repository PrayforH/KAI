// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { WeixinConnection } from "../src/components/agent-studio/weixin-connection";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root; let host: HTMLDivElement;
const fetcher = vi.fn();
const empty = { bound: false, enabled: false, online: false, state: "disconnected", loginStatus: null as string | null, qrSvg: null as string | null };
const waiting = { ...empty, loginId: "private-qr-id", loginStatus: "wait", qrSvg: '<svg xmlns="http://www.w3.org/2000/svg"><path/></svg>', expiresAt: new Date(Date.now()+300000).toISOString() };
beforeEach(() => {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  HTMLDialogElement.prototype.showModal = function () { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close = function () { this.removeAttribute("open"); };
  fetcher.mockReset(); fetcher.mockImplementation(async () => Response.json(empty)); vi.stubGlobal("fetch", fetcher);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.unstubAllGlobals(); vi.useRealTimers(); });
async function click(label: string) { const button = [...host.querySelectorAll("button")].find(b => b.textContent === label); expect(button).toBeTruthy(); await act(async () => button!.click()); }
it("opens an authenticated QR flow without developer credentials", async () => {
  let status: typeof empty = empty;
  fetcher.mockImplementation(async (url: string) => { if (url.endsWith("/login")) status = waiting; return Response.json(status); });
  await act(async () => root.render(<WeixinConnection />)); await click("连接微信");
  expect(host.querySelector('dialog[open] img')?.getAttribute("src")).toContain("data:image/svg+xml");
  expect(host.textContent).toContain("关联当前登录的 KAI 账号");
  expect(host.querySelector('input[type="password"]')).toBeNull();
  expect(fetcher.mock.calls.find(([url]) => url.endsWith("/login"))?.[1].method).toBe("POST");
});
it("shows QR expiration without a stale code", async () => {
  fetcher.mockImplementation(async () => Response.json({ ...waiting, loginStatus: "expired", qrSvg: null }));
  await act(async () => root.render(<WeixinConnection />)); await click("连接微信");
  expect(host.querySelector('dialog[open] img')).toBeNull(); expect(host.textContent).toContain("重新生成微信二维码");
});
it("replaces the QR with success only after pairing is confirmed", async () => {
  vi.useFakeTimers(); let status: typeof empty = waiting;
  fetcher.mockImplementation(async () => Response.json(status));
  await act(async () => root.render(<WeixinConnection />)); await click("连接微信");
  status = { ...waiting, enabled: true, bound: true, online: true, loginStatus: "confirmed", qrSvg: null };
  await act(async () => vi.advanceTimersByTimeAsync(2500));
  expect(host.querySelector('dialog[open]')?.textContent).toContain("微信已绑定"); expect(host.querySelector('dialog[open] img')).toBeNull();
});
it("requires explicit disconnect and retains connection on failure", async () => {
  fetcher.mockImplementation(async (url: string) => url.endsWith("/disconnect") ? new Response(null, { status: 503 }) : Response.json({ ...empty, enabled: true, bound: true, online: true }));
  await act(async () => root.render(<WeixinConnection />)); await click("断开微信");
  expect(fetcher.mock.calls.some(([url]) => url.endsWith("/disconnect"))).toBe(false);
  await click("确认断开微信"); expect(host.textContent).toContain("已关联本人 KAI 账号"); expect(host.querySelector('[role="alert"]')).toBeTruthy();
});
it("keeps unverified chat pending in diagnostics", async () => {
  fetcher.mockImplementation(async (url: string) => Response.json(url.endsWith("/diagnose") ? { checkedAt: new Date().toISOString(), checks: [{ key: "chat", label: "助理会话", status: "pending", detail: "请发消息验证" }] } : { ...empty, bound: true }));
  await act(async () => root.render(<WeixinConnection />)); await click("微信连接诊断");
  expect(host.querySelector('[data-state="pending"]')?.textContent).toContain("助理会话");
});
