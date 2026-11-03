// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ComputerDesktop } from "../src/components/computer-desktop";
const disconnected = vi.hoisted(() => vi.fn());
const sockets = vi.hoisted(() => [] as string[]);
vi.mock("@novnc/novnc", () => ({ default: class {
  constructor(_screen: unknown, url: string) { sockets.push(url); }
  viewOnly = true;
  disconnect = disconnected;
  focus() {}
  addEventListener() {}
} }));
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
const cloud = { active_target: "cloud", targets: [{ target_id: "cloud", label: "云电脑", binding_key: "c".repeat(64) }, { target_id: "local", label: "本地电脑", binding_key: "a".repeat(64) }] };
let refused = false, current = "cloud";
let host: HTMLDivElement, root: Root;
const requests: string[] = [];
async function flush() { for (let i = 0; i < 10; i++) await act(async () => { await Promise.resolve(); }); }
beforeEach(() => {
  requests.length = 0; sockets.length = 0; refused = false; current = "cloud"; disconnected.mockClear();
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    requests.push(url);
    if (url.endsWith("/targets/select")) {
      expect(JSON.parse(init?.body as string)).toEqual({ target_id: "local" });
      if (!refused) current = "local";
      return Response.json(refused ? { detail: "busy" } : { ...cloud, active_target: current }, { status: refused ? 409 : 200 });
    }
    if (url.endsWith("/targets")) return Response.json({ ...cloud, active_target: current });
    if (url.endsWith("/desktop")) return Response.json({ state: "human", binding_key: (current === "cloud" ? "c" : "a").repeat(64) });
    return Response.json({ enabled: true });
  }));
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.unstubAllGlobals(); });
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
