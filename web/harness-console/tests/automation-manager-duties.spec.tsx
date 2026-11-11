// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const { push, assistantRequest, client } = vi.hoisted(() => ({
  push: vi.fn(),
  assistantRequest: vi.fn(),
  client: {
    list: vi.fn(),
    dutySchedules: vi.fn(),
    records: vi.fn(),
    create: vi.fn(),
    update: vi.fn(),
    pause: vi.fn(),
    enable: vi.fn(),
    run: vi.fn(),
    remove: vi.fn(),
  },
}));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push }) }));
vi.mock("../src/lib/assistant-client", () => ({
  assistantRequest: (...args: unknown[]) => assistantRequest(...args),
}));
vi.mock("../src/lib/studio-client", () => ({ automationClient: client }));

vi.mock("../src/components/duty-schedule-control", () => ({
  DutyScheduleControl: ({ duty }: { duty: { title: string } }) =>
    <div data-testid="duty-schedule-control">排程编辑器：{duty.title}</div>,
}));

import { AutomationManager } from "../src/components/agent-studio/automation-manager";
import type { ApiAutomationTask, ApiSchedule } from "../src/lib/studio-client";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const task: ApiAutomationTask = {
  tenantId: "tenant",
  taskId: "task_1",
  userId: "user",
  name: "每周工作周报",
  prompt: "汇总本周进展",
  model: "auto",
  workspaceId: null,
  permission: "restricted",
  schedule: { type: "cron", cron: "0 18 * * 5", timezone: "Asia/Shanghai" },
  validity: { type: "forever" },
  status: "active",
  nextRunAt: "2026-11-13T10:00:00Z",
  lastRunAt: null,
  revision: 1,
  createdAt: "2026-10-01T00:00:00Z",
  updatedAt: "2026-10-01T00:00:00Z",
};

const duty: ApiSchedule = {
  source: "objective",
  sourceId: "duty_1",
  title: "每日巡检",
  status: "active",
  revision: 2,
  nextRunAt: "2026-11-11T09:00:00Z",
  timing: { kind: "cron", expression: "0 9 * * *", timezone: "Asia/Shanghai" },
};

let root: Root | undefined;
let host: HTMLDivElement;

beforeEach(() => {
  push.mockReset();
  assistantRequest.mockReset();
  Object.values(client).forEach((fn) => fn.mockReset());
  client.list.mockResolvedValue([]);
  client.dutySchedules.mockResolvedValue([]);
  client.records.mockResolvedValue([]);
  vi.stubGlobal("fetch", async () => Response.json([]));
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  if (root) act(() => root!.unmount());
  host.remove();
  root = undefined;
  vi.unstubAllGlobals();
});

async function flush() {
  await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
}

async function render() {
  await act(async () => { root!.render(<AutomationManager />); });
  await flush();
}

function buttons() {
  return [...host.querySelectorAll("button")];
}

async function click(match: (button: HTMLButtonElement) => boolean) {
  const button = buttons().find(match);
  if (!button) throw new Error(`button not found; page says: ${host.textContent}`);
  await act(async () => { button.click(); });
  await flush();
}

it("lists duty executions inside 运行记录 and keeps them off the duty card", async () => {
  client.dutySchedules.mockResolvedValue([duty]);
  assistantRequest.mockResolvedValue([{
    executionId: "execution_1",
    threadId: "thread_2",
    runId: "run_2",
    status: "succeeded",
    summary: "巡检通过",
    createdAt: "2026-11-11T09:00:00Z",
    triggerKey: "schedule:cron",
  }]);
  await render();

  // The duty card is a summary now; execution history lives in the tab.
  expect(host.textContent).not.toContain("执行记录");
  expect(host.textContent).toContain("每日巡检");

  await click((button) => button.textContent?.includes("运行记录") ?? false);
  expect(host.textContent).toContain("巡检通过");
  expect(host.textContent).toContain("Bot 职责 · 定时");
  expect(assistantRequest).toHaveBeenCalledWith("objectives/duty_1/executions");
});

it("moves task actions into the edit panel", async () => {
  client.list.mockResolvedValue([task]);
  client.run.mockResolvedValue({});
  await render();

  expect(buttons().some((button) => button.textContent === "立即执行")).toBe(false);
  expect(buttons().some((button) => button.textContent === "暂停")).toBe(false);

  await click((button) => button.getAttribute("aria-label") === "编辑 每周工作周报");
  expect(host.textContent).toContain("编辑自动化任务");
  expect(buttons().map((button) => button.textContent)).toEqual(
    expect.arrayContaining(["立即执行", "暂停", "删除", "取消", "保存"]),
  );

  await click((button) => button.textContent === "立即执行");
  expect(client.run).toHaveBeenCalledWith("task_1");
});

it("moves duty schedule editing and pause into the same panel", async () => {
  client.dutySchedules.mockResolvedValue([duty]);
  assistantRequest.mockResolvedValue({ status: "paused", revision: 3 });
  await render();

  expect(buttons().some((button) => button.textContent === "暂停")).toBe(false);
  expect(host.querySelector("[data-testid='duty-schedule-control']")).toBeNull();

  await click((button) => button.getAttribute("aria-label") === "编辑 每日巡检 的排程");
  expect(host.textContent).toContain("编辑职责排程");
  expect(host.textContent).toContain("排程编辑器：每日巡检");

  await click((button) => button.textContent === "暂停职责");
  expect(assistantRequest).toHaveBeenCalledWith(
    "objectives/duty_1/actions",
    "POST",
    expect.objectContaining({ action: "pause", expectedRevision: 2 }),
  );
});

it("shows templates only while the list is empty", async () => {
  client.list.mockResolvedValue([task]);
  await render();
  expect(host.textContent).not.toContain("自动化任务模版");

  act(() => root!.unmount());
  host.replaceChildren();
  root = createRoot(host);
  client.list.mockResolvedValue([]);
  await render();
  expect(host.textContent).toContain("自动化任务模版");
});
