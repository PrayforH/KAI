// @vitest-environment jsdom
import { act, StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { AuthProvider } from "../src/components/auth-provider";
import { invalidateClientReads } from "../src/lib/client-read-cache";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
it("shares StrictMode verification, sends no idle polls, tolerates 503, and blocks on 401 after remount", async () => {
  vi.useFakeTimers(); invalidateClientReads();
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({
    user:{user_id:"u1",display_name:"User"}, membership:{tenant_id:"t1",user_id:"u1",role:"member"}, password_enabled:true,
  })));
  vi.stubGlobal("fetch",fetcher);
  const host=document.createElement("div"); document.body.append(host); let root=createRoot(host);
  try {
    await act(async () => root.render(<StrictMode><AuthProvider><span>Workspace</span></AuthProvider></StrictMode>));
    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(600_000); });
    expect(fetcher).toHaveBeenCalledTimes(1);
    fetcher.mockResolvedValue(new Response("unavailable",{status:503}));
    await act(async () => { window.dispatchEvent(new Event("focus")); });
    expect(host.querySelector('[role="alertdialog"]')).toBeNull();
    act(() => root.unmount()); root=createRoot(host);
    fetcher.mockResolvedValue(new Response("unauthorized",{status:401,headers:{"x-harness-auth-error":"session_replaced"}}));
    await act(async () => root.render(<StrictMode><AuthProvider><span>Workspace</span></AuthProvider></StrictMode>));
    expect(host.querySelector('[role="alertdialog"]')?.textContent).toContain("账号已在其他窗口或设备登录");
    expect(fetcher).toHaveBeenCalledTimes(3);
  } finally { act(() => root.unmount()); host.remove(); vi.useRealTimers(); vi.unstubAllGlobals(); }
});
