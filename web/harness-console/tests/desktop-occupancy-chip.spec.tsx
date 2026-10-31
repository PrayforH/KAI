// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { DesktopOccupancyChip } from "../src/components/desktop-occupancy-chip";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const fetchMock = vi.fn();

const holder = {
  objective_id: "objective_budget_review_123456",
  execution_id: "execution_holder",
  objective_title: "预算受限验收职责检查",
  acquired_at: "2026-10-31T10:00:00Z",
  expires_at: "2026-10-31T10:15:00Z",
};

function waiting(index: number) {
  return {
    objective_id: `objective_wait_${index}`,
    execution_id: `execution_wait_${index}`,
    objective_title: `排队职责 ${index}`,
    position: index + 1,
    wait_reason: "单执行者占用中（另一项职责正在使用共享桌面）",
  };
}

function respond(payload: unknown) {
  fetchMock.mockResolvedValue({
    ok: true,
    status: 200,
    json: async () => payload,
  });
}

let root: Root | undefined;
let host: HTMLDivElement;

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined;
  host.remove();
  vi.unstubAllGlobals();
});

async function mount() {
  await act(async () => {
    root!.render(<DesktopOccupancyChip />);
  });
  await act(async () => {});
}

describe("desktop occupancy chip", () => {
  it("shows who holds the desktop, the queue position and the wait reason", async () => {
    respond({ holder, waiting: [waiting(0), waiting(1)] });
    await mount();

    const chip = host.querySelector(".desktop-occupancy-chip");
    expect(chip).not.toBeNull();
    expect(chip?.getAttribute("data-occupancy")).toBe("held");
    expect(chip?.getAttribute("role")).toBe("status");
    expect(chip?.getAttribute("aria-live")).toBe("polite");
    expect(chip?.textContent).toContain("预算受限验收职责检查");
    expect(chip?.textContent).toContain("排队中 #1");
    expect(chip?.textContent).toContain("共 2");
    expect(chip?.textContent).toContain("单执行者占用中（另一项职责正在使用共享桌面）");
    expect(chip?.getAttribute("data-queue-position")).toBe("1");
    expect(chip?.getAttribute("data-wait-reason")).toContain("单执行者占用中");
    expect(chip?.getAttribute("aria-label")).toContain("预算受限验收职责检查");
    expect(chip?.getAttribute("aria-label")).toContain("排队中第 1 位");
    expect(chip?.getAttribute("aria-label")).toContain("等待原因：单执行者占用中");

    // 只读投影：带 no-store，且只打这一个端点。
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/studio/assistant/desktop/occupancy",
      expect.objectContaining({ cache: "no-store" }),
    );
  });

  it("shows the queue position when nobody holds the desktop", async () => {
    respond({ holder: null, waiting: [{ ...waiting(0), position: 2 }] });
    await mount();
    const chip = host.querySelector(".desktop-occupancy-chip");
    expect(chip?.getAttribute("data-occupancy")).toBe("queued");
    expect(chip?.textContent).toContain("排队中 #2");
    expect(chip?.getAttribute("data-queue-position")).toBe("2");
    expect(chip?.getAttribute("aria-label")).toContain("第 2 位");
  });

  it("falls back to the queue count for old payloads without position", async () => {
    const legacy = waiting(0);
    delete (legacy as { position?: number }).position;
    respond({ holder: null, waiting: [legacy] });
    await mount();
    const chip = host.querySelector(".desktop-occupancy-chip");
    expect(chip?.textContent).toContain("桌面排队 1");
    expect(chip?.textContent).not.toContain("#");
    expect(chip?.getAttribute("data-queue-position")).toBeNull();
    // 原因仍可读（旧载荷有 wait_reason 时照常展示）。
    expect(chip?.textContent).toContain("单执行者占用中");
  });

  it("stays hidden when the desktop is idle", async () => {
    respond({ holder: null, waiting: [] });
    await mount();
    expect(host.querySelector(".desktop-occupancy-chip")).toBeNull();
  });

  it("falls back to a short objective id when the holder has no title", async () => {
    respond({
      holder: { ...holder, objective_title: "" },
      waiting: [],
    });
    await mount();
    const chip = host.querySelector(".desktop-occupancy-chip");
    expect(chip?.textContent).toContain("objective_bu");
    expect(chip?.textContent).not.toContain("排队");
  });

  it("keeps the header quiet when the projection fails", async () => {
    fetchMock.mockRejectedValue(new Error("offline"));
    await mount();
    expect(host.querySelector(".desktop-occupancy-chip")).toBeNull();
  });
});
