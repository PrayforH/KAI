// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const assistantRequest = vi.fn();

vi.mock("../src/lib/assistant-client", () => ({
  assistantRequest: (...args: unknown[]) => assistantRequest(...args),
}));

import { HandoverInlineRows } from "../src/components/handover-row";
import { handoverStore } from "../src/lib/handover-store";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const specialistId = "specialist_pending_1";
const activeId = "specialist_already_active";

const detail = {
  specialist: {
    specialistId,
    name: "休息提醒",
    description: "例行提醒",
    status: "needs_input",
    revision: 3,
    config: {},
  },
  threadId: "assistant_objective_1",
  primaryObjectiveId: "objective_1",
  duties: [
    {
      objectiveId: "objective_1",
      title: "每天提醒",
      status: "active",
      schedule: { everyMinutes: 1440 },
    },
  ],
};

let root: Root | undefined;
let host: HTMLDivElement;

beforeEach(() => {
  assistantRequest.mockReset();
  assistantRequest.mockImplementation(async (path: string, method = "GET") => {
    if (path === "handovers") {
      return [{ specialistId, status: "pending_input", createdAt: "2026-11-01T00:00:00Z" }];
    }
    if (path === "specialists") return [{ specialistId, name: "休息提醒" }];
    if (path === `specialists/${specialistId}` && method === "GET") return detail;
    if (path === `specialists/${activeId}` && method === "GET") {
      return {
        ...detail,
        specialist: { ...detail.specialist, specialistId: activeId, name: "天气提醒", status: "active" },
      };
    }
    if (path === `specialists/${specialistId}/config`) {
      return { ...detail.specialist, status: "ready", revision: 4 };
    }
    if (path === `specialists/${specialistId}/status`) {
      return { ...detail.specialist, status: "active", revision: 5 };
    }
    throw new Error(`unexpected request: ${method} ${path}`);
  });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined;
  host.remove();
});

async function flush(rounds = 8) {
  for (let index = 0; index < rounds; index += 1) {
    await act(async () => {
      await Promise.resolve();
    });
  }
}

function button(label: string): HTMLButtonElement | null {
  return (
    [...host.querySelectorAll("button")].find((node) =>
      (node.textContent ?? "").includes(label),
    ) ?? null
  );
}

describe("pending_input handover in the main session", () => {
  it("mounts the setup card inline and enables without navigating to ?bot=", async () => {
    const assign = vi.fn();
    const original = window.location.assign;
    let assignPatched = true;
    try {
      Object.defineProperty(window.location, "assign", {
        configurable: true,
        writable: true,
        value: assign,
      });
    } catch {
      assignPatched = false;
    }

    await act(async () => {
      root!.render(<HandoverInlineRows />);
    });
    await flush();

    // 转交行 + 同一张补齐卡都出现在主会话里。
    expect(host.textContent ?? "").toContain("待补充 ·");
    expect(host.textContent ?? "").toContain("「休息提醒」");
    expect(host.querySelector(".specialist-setup-card")).not.toBeNull();
    expect(host.textContent ?? "").toContain("还差两步就能开始跑");

    await act(async () => button("每小时")!.click());
    await act(async () => button("保存并启用")!.click());
    await flush();

    const calls = assistantRequest.mock.calls.map(([path, method]) => `${String(method)} ${String(path)}`);
    const configIndex = calls.findIndex((call) => call.endsWith(`/config`));
    const statusIndex = calls.findIndex((call) => call.endsWith(`/status`));
    expect(configIndex).toBeGreaterThan(-1);
    expect(statusIndex).toBeGreaterThan(configIndex);
    const statusCall = assistantRequest.mock.calls.find(([path]) =>
      String(path).endsWith("/status"),
    );
    expect((statusCall?.[2] as { status?: string }).status).toBe("active");

    // 成功后卡片收起、行态不再「待补充」，rail 也收到刷新事件。
    expect(host.querySelector(".specialist-setup-card")).toBeNull();
    expect(host.textContent ?? "").toContain("已安排");
    expect(host.textContent ?? "").not.toContain("待补充");
    expect(handoverStore.snapshot().some((item) => item.status === "pending_input")).toBe(false);

    if (assignPatched) {
      expect(assign).not.toHaveBeenCalled();
      Object.defineProperty(window.location, "assign", {
        configurable: true,
        writable: true,
        value: original,
      });
    } else {
      // jsdom 不允许改写 location.assign：至少确认还停在当前页。
      expect(window.location.pathname).toBe("/");
    }
  });

  it("normalizes a stale pending_input row when the bot is already active", async () => {
    await act(async () => {
      root!.render(<HandoverInlineRows />);
    });
    await flush(2);
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent("kai:handover", {
          detail: { specialist_id: activeId, specialist_name: "天气提醒", status: "pending_input" },
        }),
      );
    });
    await flush(8);

    const row = [...host.querySelectorAll(".handover-inline-item")].find((node) =>
      (node.textContent ?? "").includes("天气提醒"),
    );
    expect(row?.textContent).toContain("已安排");
    expect(row?.textContent).not.toContain("待补充");
    expect(row?.querySelector(".specialist-setup-card")).toBeNull();
  });

  it("leaves non-pending handovers as plain links", async () => {
    await act(async () => {
      root!.render(<HandoverInlineRows />);
    });
    await flush(2);
    await act(async () => {
      window.dispatchEvent(
        new CustomEvent("kai:handover", {
          detail: { specialist_id: "specialist_done", specialist_name: "行业快报监控", status: "applied" },
        }),
      );
    });
    await flush(2);

    const row = [...host.querySelectorAll(".handover-inline-item")].find((node) =>
      (node.textContent ?? "").includes("行业快报监控"),
    );
    expect(row?.textContent).toContain("已安排");
    expect(row?.querySelector(".specialist-setup-card")).toBeNull();
    expect(row?.querySelector("a")?.getAttribute("href")).toBe(
      "/assistant?bot=specialist_done",
    );
  });
});
