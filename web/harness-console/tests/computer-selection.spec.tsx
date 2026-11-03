// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ComputerDesktop } from "../src/components/computer-desktop";
const disconnected = vi.hoisted(() => vi.fn());
vi.mock("@novnc/novnc", () => ({ default: class {
  viewOnly = true;
  disconnect = disconnected;
  focus() {}
  addEventListener() {}
} }));
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
const cloud = { active_target: "cloud", targets: [{ target_id: "cloud", label: "云电脑" }, { target_id: "local", label: "本地电脑" }] };
let refused = false, current = "cloud";
let host: HTMLDivElement, root: Root;
const requests: string[] = [];
async function flush() { for (let i = 0; i < 10; i++) await act(async () => { await Promise.resolve(); }); }
beforeEach(() => {
  requests.length = 0; refused = false; current = "cloud"; disconnected.mockClear();
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    requests.push(url);
    if (url.endsWith("/targets/select")) {
      expect(JSON.parse(init?.body as string)).toEqual({ target_id: "local" });
      if (!refused) current = "local";
      return Response.json(refused ? { detail: "busy" } : { ...cloud, active_target: current }, { status: refused ? 409 : 200 });
    }
    if (url.endsWith("/targets")) return Response.json({ ...cloud, active_target: current });
    if (url.endsWith("/desktop")) return Response.json({ state: "observe" });
    return Response.json({ enabled: true });
  }));
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.unstubAllGlobals(); });
async function mountAndSelect() {
  await act(async () => root.render(<ComputerDesktop />)); await flush();
  const select = host.querySelector('select[aria-label="选择电脑"]') as HTMLSelectElement;
  expect(select).not.toBeNull();
  await act(async () => { select.value = "local"; select.dispatchEvent(new Event("change", { bubbles: true })); });
  await flush();
  return select;
}
describe("owner computer selection", () => {
  it("reconnects the desktop after a successful authorized selection", async () => {
    const select = await mountAndSelect();
    expect(requests).toContain("/api/studio/assistant/computer/targets/select");
    expect(disconnected).toHaveBeenCalled();
    expect(select.value).toBe("local");
  });
  it("keeps the original desktop when a live operation refuses switching", async () => {
    refused = true;
    const select = await mountAndSelect();
    expect(host.querySelector('[role="alert"]')?.textContent).toContain("请等待当前操作结束并交还控制后再切换");
    expect(select.value).toBe("cloud");
    expect(disconnected).not.toHaveBeenCalled();
  });
});
