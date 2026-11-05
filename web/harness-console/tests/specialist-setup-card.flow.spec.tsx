// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const assistantRequest = vi.fn();

vi.mock("../src/lib/assistant-client", () => ({
  assistantRequest: (...args: unknown[]) => assistantRequest(...args),
}));

import { SpecialistSetupCard } from "../src/components/specialist-setup-card";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const detail = {
  specialist: {
    specialistId: "specialist-1",
    name: "休息提醒",
    description: "每天提醒",
    status: "needs_input",
    revision: 3,
    config: {},
  },
  threadId: "assistant_objective_1",
  primaryObjectiveId: "objective_1",
  duties: [{objectiveId: "objective_1", hasExecutions: false}],
};

let root: Root | undefined;
let host: HTMLDivElement;

beforeEach(() => {
  assistantRequest.mockReset();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined;
  host.remove();
});

function buttons(): HTMLButtonElement[] {
  return [...host.querySelectorAll("button")];
}

function click(label: string) {
  const target = buttons().find((b) => (b.textContent || "").includes(label));
  if (!target) throw new Error(`button not found: ${label}`);
  target.dispatchEvent(new MouseEvent("click", { bubbles: true }));
}

describe("SpecialistSetupCard flow", () => {
  it("saves config then activates with the new revision, then reports queued", async () => {
    assistantRequest.mockImplementation(async (path: string, method?: string) => {
      // 组件读详情时不传 method（client 默认 GET）。
      if (method === undefined || method === "GET") return detail;
      if (path.endsWith("/config")) {
        return { ...detail.specialist, status: "ready", revision: 4, config: { schedule_minutes: 60, notify_channel: "inbox" } };
      }
      if (path.endsWith("/status")) {
        return { ...detail.specialist, status: "active", revision: 5, config: { schedule_minutes: 60, notify_channel: "inbox" } };
      }
      return detail;
    });
    const ready = vi.fn();
    await act(async () => {
      root!.render(<SpecialistSetupCard specialistId="specialist-1" onReady={ready} />);
    });
    // 首次交办无频率要求。
    expect(host.textContent).toContain("开始这次交办");
    expect(host.textContent).not.toContain("检查频率");
    expect(host.textContent).toContain("开始这次交办");

    await act(async () => click("开始这次交办"));
    await act(async () => {});

    const calls = assistantRequest.mock.calls.map(([path, method, body]) => ({
      path: String(path),
      method: String(method),
      body: body as Record<string, unknown>,
    }));
    const configCall = calls.find((c) => c.path.endsWith("/config"));
    const statusCall = calls.find((c) => c.path.endsWith("/status"));
    expect(configCall).toBeTruthy();
    expect(statusCall).toBeTruthy();
    // 顺序：先 config 再 status，且 status 用 config 返回的 revision。
    expect(calls.indexOf(configCall!)).toBeLessThan(calls.indexOf(statusCall!));
    expect(configCall!.body.expectedRevision).toBe(3);
    expect((configCall!.body.config as Record<string, unknown>).schedule_minutes).toBeUndefined();
    expect((configCall!.body.config as Record<string, unknown>).notify_channel).toBe("inbox");
    expect(statusCall!.body.expectedRevision).toBe(4);
    expect(statusCall!.body.status).toBe("active");
    const executionCall = calls.find(c => c.path === "objectives/objective_1/executions");
    expect(executionCall?.method).toBe("POST");
    expect(assistantRequest.mock.calls.find(([path]) => String(path).endsWith("/executions"))?.[3]).toBe("first-handover:objective_1");
    expect(calls.some(c => c.path.endsWith("/schedule"))).toBe(false);

    expect(host.textContent).toContain("本次交办已安排");
    expect(ready).toHaveBeenCalled();
  });

  it("renders nothing for an already-active bot instead of a stale queued banner", async () => {
    assistantRequest.mockImplementation(async () => ({
      ...detail,
      duties: [{objectiveId: "objective_1", hasExecutions: true}],
      specialist: {
        ...detail.specialist,
        status: "active",
        revision: 6,
        config: { schedule_minutes: 1440, notify_channel: "inbox" },
      },
    }));
    await act(async () => {
      root!.render(<SpecialistSetupCard specialistId="specialist-1" />);
    });
    await act(async () => {});
    expect(host.textContent).toBe("");
    expect(buttons()).toHaveLength(0);
  });

  it("shows the server error and keeps the button usable when activation fails", async () => {
    assistantRequest.mockImplementation(async (path: string, method?: string) => {
      if (method === undefined || method === "GET") return detail;
      if (path.endsWith("/config")) return { ...detail.specialist, status: "ready", revision: 4 };
      throw new Error("该子助理还没有绑定职责，先转交一项职责再启用");
    });
    await act(async () => {
      root!.render(<SpecialistSetupCard specialistId="specialist-1" />);
    });
    await act(async () => click("开始这次交办"));
    await act(async () => {});
    expect(host.textContent).toContain("还没有绑定职责");
    const submit = buttons().find((b) => (b.textContent || "").includes("开始这次交办"));
    expect(submit?.disabled).toBe(false);
  });
});
