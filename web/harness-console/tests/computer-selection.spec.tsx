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
let controlState = "human", controlFailure = 0, readFailure = false;
let delayedRead: Promise<Response> | null = null;
let host: HTMLDivElement, root: Root;
const requests: string[] = [];
async function flush() { for (let i = 0; i < 10; i++) await act(async () => { await Promise.resolve(); }); }
beforeEach(() => {
  requests.length = 0; sockets.length = 0; frames.length = 0;
  refused = false; current = "cloud"; controlState = "human"; controlFailure = 0; readFailure = false; delayedRead = null; disconnected.mockClear();
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    requests.push(url);
    if (url.endsWith("/targets/select")) {
      expect(JSON.parse(init?.body as string)).toEqual({ target_id: "local" });
      if (!refused) current = "local";
      return Response.json(refused ? { detail: "busy" } : { ...cloud, active_target: current }, { status: refused ? 409 : 200 });
    }
    if (url.endsWith("/targets")) return Response.json({ ...cloud, active_target: current });
    if (url.endsWith("/desktop")) {
      if (delayedRead) { const pending = delayedRead; delayedRead = null; return pending; }
      return Response.json({ state: controlState, binding_key: (current === "cloud" ? "c" : "a").repeat(64) }, { status: readFailure ? 503 : 200 });
    }
    if (url.includes("/desktop/")) {
      if (controlFailure) return Response.json({}, { status: controlFailure });
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
    expect(host.textContent).toContain("当前仅观看本地电脑；助理使用的是云电脑");
    await click("交还云电脑");
    expect(requests).toContain(`/api/studio/assistant/computer/desktop/return?binding_key=${"c".repeat(64)}`);
    expect(current).toBe("cloud");
    expect(frames.at(-1)!.viewOnly).toBe(true);
    expect(host.textContent).not.toContain("云电脑由你控制");
  });
  it("keeps returning ownership available after the viewer disconnects", async () => {
    await mount();
    await act(async () => frames.at(-1)!.emit("disconnect"));
    expect(host.textContent).toContain("电脑暂未连接");
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
    expect(host.textContent).toContain("交接完成后才能操作");
    expect(frames.at(-1)!.viewOnly).toBe(true);
    await click("取消接管");
    expect(controlState).toBe("observe");
  });
  it("stops local input and explains when control status cannot be read", async () => {
    vi.useFakeTimers();
    await mount(); expect(frames.at(-1)!.viewOnly).toBe(false);
    readFailure = true;
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); }); await flush();
    expect(host.textContent).toContain("控制状态暂时无法确认");
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
