// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ComputerDesktop } from "../src/components/computer-desktop";
const disconnected = vi.hoisted(() => vi.fn());
const sockets = vi.hoisted(() => [] as string[]);
const frames = vi.hoisted(() => [] as { viewOnly: boolean; emit: (event: string) => void }[]);
vi.mock("@novnc/novnc", () => ({ default: class {
  listeners: Record<string, () => void> = {};
  constructor(_screen: unknown, url: string) { sockets.push(url); frames.push(this); }
  emit(event: string) { this.listeners[event]?.(); }
  viewOnly = true;
  disconnect = disconnected;
  focus() {}
  addEventListener(event: string, callback: () => void) { this.listeners[event] = callback; }
} }));
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
const cloud = { active_target: "cloud", targets: [{ target_id: "cloud", label: "云电脑", binding_key: "c".repeat(64) }, { target_id: "local", label: "本地电脑", binding_key: "a".repeat(64) }] };
let refused = false, current = "cloud";
let controlState = "human", controlFailure = 0, readFailure = false, sessionStatus = 200;
let delayedRead: Promise<Response> | null = null;
let startFailure = false;
let delayedStart: Promise<Response> | null = null;
const botControls = new Map<string, { state: string; owner?: string | null }>();
let host: HTMLDivElement, root: Root;
const requests: string[] = [];
async function flush() { for (let i = 0; i < 10; i++) await act(async () => { await Promise.resolve(); }); }
beforeEach(() => {
  requests.length = 0; sockets.length = 0; frames.length = 0; startFailure = false; delayedStart = null;
  window.sessionStorage.clear();
  botControls.clear();
  refused = false; current = "cloud"; controlState = "human"; controlFailure = 0; readFailure = false; sessionStatus = 200; delayedRead = null; disconnected.mockClear();
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    requests.push(url);
    const request = new URL(url, "https://kai.test");
    const desktopId = request.searchParams.get("desktop_id");
    if (url === "/api/auth/session") return Response.json({}, { status: sessionStatus });
    if (url.includes("/desktops/start")) {
      expect(init?.method).toBe("POST");
      if (delayedStart) return delayedStart;
      if (startFailure) return Response.json({detail:"云桌面启动未确认"}, { status: 503 });
      const hash = request.searchParams.get("bot_id") === "specialist_b" ? "b" : "a";
      return Response.json({ desktopId: `desktop_${hash.repeat(24)}`, state: "idle" });
    }
    if (url.includes("/desktops/preview")) return Response.json({}, { status: 404 });
    if (url.endsWith("/targets/select")) {
      expect(JSON.parse(init?.body as string)).toEqual({ target_id: "local" });
      if (!refused) current = "local";
      return Response.json(refused ? { detail: "busy" } : { ...cloud, active_target: current }, { status: refused ? 409 : 200 });
    }
    if (url.endsWith("/targets")) return Response.json({ ...cloud, active_target: current });
    if (request.pathname.endsWith("/desktop")) {
      if (desktopId) return Response.json({
        ...botControls.get(desktopId) ?? { state: "observe" },
        desktop_id: desktopId, binding_key: "c".repeat(64),
      });
      if (delayedRead) { const pending = delayedRead; delayedRead = null; return pending; }
      return Response.json({ state: controlState, binding_key: (current === "cloud" ? "c" : "a").repeat(64) }, { status: readFailure ? 503 : 200 });
    }
    if (url.includes("/desktop/")) {
      if (controlFailure) return Response.json({}, { status: controlFailure });
      if (desktopId) {
        const state = url.includes("/return?") ? "observe" : "takeover_pending";
        const owner = state === "observe" ? null : request.searchParams.get("viewer_id");
        botControls.set(desktopId, { state, owner });
        return Response.json({ state, owner, desktop_id: desktopId, binding_key: null });
      }
      controlState = url.includes("/return?") ? "observe" : "takeover_pending";
      return Response.json({ state: controlState, binding_key: null });
    }
    return Response.json({ enabled: true });
  }));
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.unstubAllGlobals(); vi.useRealTimers(); });
async function mountAndSelect() {
  await act(async () => root.render(<ComputerDesktop />)); await flush();
  const select = host.querySelector('select[aria-label="查看电脑"]') as HTMLSelectElement;
  expect(select).not.toBeNull();
  await act(async () => { select.value = "local"; select.dispatchEvent(new Event("change", { bubbles: true })); });
  await flush();
  return select;
}
describe("owner computer selection", () => {
  it("changes the viewed desktop without moving a running assistant", async () => {
    const select = await mountAndSelect();
    expect(requests).not.toContain("/api/studio/assistant/computer/targets/select");
    expect(disconnected).toHaveBeenCalled();
    expect(select.value).toBe("local");
    expect(sockets.at(-1)).toContain("target=local");
    expect(current).toBe("cloud");
    expect([...host.querySelectorAll("button")].some(node => node.textContent === "接管")).toBe(false);
  });
  it("refuses changing execution during a live operation but keeps viewing available", async () => {
    refused = true;
    const select = await mountAndSelect();
    await act(async () => [...host.querySelectorAll("button")].find(node => node.textContent === "供助理使用")!.click());
    expect(host.querySelector('[role="alert"]')?.textContent).toContain("当前工作仍在原电脑执行");
    expect(select.value).toBe("local");
    expect(current).toBe("cloud");
    await act(async () => { select.value = "cloud"; select.dispatchEvent(new Event("change", { bubbles: true })); });
    await flush();
    expect(select.value).toBe("cloud");
    expect(sockets.at(-1)).toContain("target=cloud");
  });
  it("changes the execution computer only through the explicit action", async () => {
    await mountAndSelect();
    await act(async () => [...host.querySelectorAll("button")].find(node => node.textContent === "供助理使用")!.click());
    await flush();
    expect(current).toBe("local");
    expect(requests.filter(url => url.endsWith("targets/select"))).toHaveLength(1);
  });
});

async function mount() {
  await act(async () => root.render(<ComputerDesktop />)); await flush();
  await act(async () => frames.at(-1)!.emit("connect"));
}
async function click(label: string) {
  const button = [...host.querySelectorAll("button")].find(node => node.textContent === label);
  expect(button).toBeDefined();
  await act(async () => button!.click()); await flush();
}
describe("desktop ownership feedback", () => {
  it("can return the actual execution computer while viewing another target", async () => {
    await mountAndSelect();
    expect(host.textContent).toContain("供助理使用");
    expect(host.textContent).not.toContain("默认使用云电脑");
    expect(host.textContent).not.toContain("当前仅观看本地电脑");
    await click("交还云电脑");
    expect(requests).toContain(`/api/studio/assistant/computer/desktop/return?binding_key=${"c".repeat(64)}`);
    expect(current).toBe("cloud");
    expect(frames.at(-1)!.viewOnly).toBe(true);
    expect(host.textContent).not.toContain("云电脑由你控制");
  });
  it("keeps returning ownership available after the viewer disconnects", async () => {
    await mount();
    await act(async () => frames.at(-1)!.emit("disconnect"));
    expect(host.textContent).toContain("正在重新连接电脑");
    await click("交还助理");
    expect(controlState).toBe("observe");
    expect(frames.at(-1)!.viewOnly).toBe(true);
  });
  it.each([409, 503])("shows a control failure (%s) instead of silently ignoring it", async status => {
    controlState = "observe"; controlFailure = status;
    await mount(); await click("取得控制权");
    expect(host.querySelector('[role="alert"]')?.textContent).toContain(status === 409 ? "控制权或电脑已变化" : "控制权操作未成功");
    expect(frames.at(-1)!.viewOnly).toBe(true);
  });
  it("waits for confirmed handoff and lets the user cancel a pending takeover", async () => {
    controlState = "observe";
    await mount(); await click("取得控制权");
    expect(host.textContent).toContain("正在交接");
    expect(frames.at(-1)!.viewOnly).toBe(true);
    await click("取消接管");
    expect(controlState).toBe("observe");
  });
  it("stops local input and explains when control status cannot be read", async () => {
    vi.useFakeTimers();
    await mount(); expect(frames.at(-1)!.viewOnly).toBe(false);
    readFailure = true;
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); }); await flush();
    expect(host.textContent).toContain("控制状态待确认");
    expect(host.querySelector('[title^="控制状态暂时无法确认"]')).not.toBeNull();
    expect(frames.at(-1)!.viewOnly).toBe(true);
    readFailure = false; await click("重试控制状态");
    expect(host.textContent).toContain("由你控制");
    expect(frames.at(-1)!.viewOnly).toBe(false);
  });
  it("does not let a late poll restore human input after returning ownership", async () => {
    vi.useFakeTimers(); await mount();
    let resolve!: (value: Response) => void;
    delayedRead = new Promise<Response>(done => { resolve = done; });
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    await click("交还助理");
    await act(async () => { resolve(Response.json({ state: "human", binding_key: "c".repeat(64) })); }); await flush();
    expect(host.textContent).not.toContain("由你控制：");
    expect(frames.at(-1)!.viewOnly).toBe(true);
  });
});

describe("desktop session renewal", () => {
  it("refreshes login and resumes the same target after a live connection closes", async () => {
    vi.useFakeTimers(); await mount();
    const previous = frames.at(-1)!;
    requests.length = 0;
    await act(async () => previous.emit("disconnect"));
    expect(previous.viewOnly).toBe(true);
    expect(host.textContent).toContain("正在重新连接电脑");
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); }); await flush();
    expect(requests[0]).toBe("/api/auth/session");
    expect(sockets).toHaveLength(2);
    expect(sockets.at(-1)).toContain("target=cloud");
    await act(async () => frames.at(-1)!.emit("connect"));
    expect(host.textContent).toContain("已连接");
    // Late events from the old transport cannot disconnect its replacement.
    await act(async () => previous.emit("disconnect"));
    expect(host.textContent).toContain("已连接");
  });
  it("stops automatic retries when login renewal is denied", async () => {
    vi.useFakeTimers(); await mount(); sessionStatus = 401;
    await act(async () => frames.at(-1)!.emit("disconnect"));
    await act(async () => { await vi.advanceTimersByTimeAsync(1500); }); await flush();
    expect(host.textContent).toContain("请重新登录");
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(sockets).toHaveLength(1);
  });
  it("bounds transient retry attempts and leaves a manual reconnect", async () => {
    vi.useFakeTimers(); await mount(); sessionStatus = 503;
    requests.length = 0;
    await act(async () => frames.at(-1)!.emit("disconnect"));
    await act(async () => { await vi.advanceTimersByTimeAsync(40000); }); await flush();
    expect(requests.filter(url => url === "/api/auth/session")).toHaveLength(5);
    expect(host.textContent).toContain("电脑暂未连接");
    expect(host.textContent).toContain("重新连接");
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(requests.filter(url => url === "/api/auth/session")).toHaveLength(5);
  });
  it("cancels a pending retry when the view is closed", async () => {
    vi.useFakeTimers(); await mount();
    await act(async () => frames.at(-1)!.emit("disconnect"));
    requests.length = 0;
    await act(async () => root.render(null));
    await act(async () => { await vi.advanceTimersByTimeAsync(20000); });
    expect(requests).toEqual([]);
  });
  it("stops retrying instead of looping through connections that die immediately", async () => {
    vi.useFakeTimers(); await mount();
    for (let attempt = 0; attempt < 6; attempt++) {
      await act(async () => frames.at(-1)!.emit("disconnect"));
      await act(async () => { await vi.advanceTimersByTimeAsync(11000); }); await flush();
      if (attempt < 5) await act(async () => frames.at(-1)!.emit("connect"));
    }
    expect(host.textContent).toContain("电脑暂未连接");
    expect(sockets).toHaveLength(6);
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(sockets).toHaveLength(6);
  });
});


describe("Bot desktop preview", () => {
  it("keeps its viewer identity across a reload so the tab resumes its own takeover", async () => {
    vi.useFakeTimers();
    await act(async () => root.render(<ComputerDesktop botId="specialist_a" />));
    await flush();
    const first = new URL(sockets.at(-1)!).searchParams.get("viewer_id");
    expect(first).toBeTruthy();
    await act(async () => root.render(null));
    await act(async () => root.render(<ComputerDesktop botId="specialist_a" />));
    await flush();
    expect(sockets).toHaveLength(2);
    expect(new URL(sockets[1]).searchParams.get("viewer_id")).toBe(first);
  });

  it("takes over the selected Bot slot and returns to view-only when switching Bots", async () => {
    vi.useFakeTimers();
    await act(async () => root.render(<ComputerDesktop botId="specialist_a" />));
    await flush();
    const slot = `desktop_${"a".repeat(24)}`;
    const socket = new URL(sockets.at(-1)!);
    const viewer = socket.searchParams.get("viewer_id");
    expect(socket.searchParams.get("desktop")).toBe(slot);
    expect(viewer).toBeTruthy();
    expect(frames.at(-1)?.viewOnly).toBe(true);
    await act(async () => frames.at(-1)!.emit("connect"));
    await click("取得控制权");
    expect(requests.some(url => url.includes("/desktop/takeover?") && url.includes(`desktop_id=${slot}&viewer_id=${viewer}`))).toBe(true);
    expect(frames.at(-1)?.viewOnly).toBe(true);
    botControls.set(slot, { state: "human", owner: viewer });
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(frames.at(-1)?.viewOnly).toBe(false);

    await act(async () => root.render(<ComputerDesktop botId="specialist_b" />));
    await flush();
    expect(disconnected).toHaveBeenCalled();
    expect(sockets.at(-1)).toContain(`target=cloud&desktop=desktop_${"b".repeat(24)}`);
    expect(frames.at(-1)?.viewOnly).toBe(true);
    expect(requests).not.toContain("/api/studio/assistant/computer/desktop");
  });

  it("keeps the existing Remote Computer target when it is selected", async () => {
    current = "local";
    await act(async () => root.render(<ComputerDesktop botId="specialist_a" />));
    await flush();
    expect(sockets.at(-1)).toContain("target=local");
    expect(sockets.at(-1)).not.toContain("desktop=");
    expect(requests.some(url => /\/desktops\/(?:preview|start)/.test(url))).toBe(false);
  });

  it("starts the selected Bot on open and waits for the guest before connecting", async () => {
    let complete!: (response: Response) => void;
    delayedStart = new Promise(resolve => { complete = resolve; });
    await act(async () => root.render(<ComputerDesktop botId="specialist_a" />));
    await flush();
    const endpoint = "/api/studio/assistant/computer/desktops/start?bot_id=specialist_a";
    expect(requests.filter(url => url === endpoint)).toHaveLength(1);
    expect(host.textContent).toContain("正在连接电脑");
    expect(sockets).toHaveLength(0);
    complete(Response.json({ desktopId: `desktop_${"a".repeat(24)}`, state: "idle" }));
    await flush();
    expect(sockets).toHaveLength(1);
    expect(sockets[0]).toContain(`desktop=desktop_${"a".repeat(24)}`);
  });

  it("retries the guest start explicitly after a failure", async () => {
    startFailure = true;
    await act(async () => root.render(<ComputerDesktop botId="specialist_a" />));
    await flush();
    expect(host.textContent).toContain("暂时无法连接电脑");
    expect(sockets).toHaveLength(0);
    startFailure = false;
    await click("重新连接");
    expect(requests.filter(url => url.includes("/desktops/start?bot_id=specialist_a"))).toHaveLength(2);
    expect(sockets).toHaveLength(1);
  });
});
