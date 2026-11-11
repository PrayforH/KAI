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
  specialistId: "specialist_1",
  agentName: "小助手",
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

// The “more” menu renders in a portal, so look at the whole document.
function buttons() {
  return [...document.querySelectorAll("button")];
}

async function click(match: (button: HTMLButtonElement) => boolean) {
  const button = buttons().find(match);
  if (!button) throw new Error(`button not found; page says: ${document.body.textContent}`);
  await act(async () => { button.click(); });
  await flush();
}

function byText(text: string) {
  return (button: HTMLButtonElement) => button.textContent?.trim() === text;
}

function byLabel(label: string) {
  return (button: HTMLButtonElement) => button.getAttribute("aria-label") === label;
}

function switchFor(title: string) {
  return (button: HTMLButtonElement) =>
    button.getAttribute("role") === "switch" && button.getAttribute("aria-label") === `${title} 启用状态`;
}

function switchState(title: string): string | null {
  return buttons().find(switchFor(title))?.getAttribute("aria-checked") ?? null;
}

it("names merged schedules 定时任务 and shows the owning assistant", async () => {
  client.dutySchedules.mockResolvedValue([duty]);
  await render();

  expect(host.textContent).toContain("每日巡检");
  expect(host.textContent).toContain("定时任务");
  expect(host.textContent).toContain("小助手");
  expect(host.textContent).not.toContain("Bot 职责");
  expect(host.textContent).not.toContain("主助理职责");

  await click((button) => button.getAttribute("aria-label") === "编辑定时任务 每日巡检");
  expect(host.querySelector('[role="dialog"]')?.getAttribute("aria-label")).toBe("编辑定时任务");
  expect(host.textContent).toContain("排程编辑器：每日巡检");
});

it("lists duty executions inside 运行记录 with the unified label", async () => {
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

  await click(byText("运行记录"));
  expect(host.textContent).toContain("巡检通过");
  expect(host.textContent).toContain("定时任务 · 定时");
  expect(host.textContent).not.toContain("Bot 职责");
  expect(assistantRequest).toHaveBeenCalledWith("objectives/duty_1/executions");
});

it("toggles a duty schedule through the objective actions API", async () => {
  client.dutySchedules.mockResolvedValue([duty]);
  assistantRequest.mockResolvedValue({ status: "paused", revision: 3 });
  await render();

  expect(switchState("每日巡检")).toBe("true");

  await click(switchFor("每日巡检"));
  expect(assistantRequest).toHaveBeenCalledWith(
    "objectives/duty_1/actions",
    "POST",
    expect.objectContaining({ action: "pause", expectedRevision: 2 }),
  );
});

it("runs a duty once even when 立即执行 is double-clicked", async () => {
  client.dutySchedules.mockResolvedValue([duty]);
  assistantRequest.mockResolvedValue({ executionId: "execution_1" });
  await render();

  const runButton = buttons().find(byLabel("立即执行 每日巡检"));
  if (!runButton) throw new Error("run button missing");
  await act(async () => {
    runButton.click();
    runButton.click();
  });
  await flush();

  const runCalls = assistantRequest.mock.calls.filter(([path]) => path === "objectives/duty_1/executions");
  expect(runCalls).toHaveLength(1);
  expect(runCalls[0][1]).toBe("POST");
  expect(runCalls[0][2]).toBeUndefined();
  expect(runCalls[0][3]).toEqual(expect.any(String));
  expect((runCalls[0][3] as string).length).toBeGreaterThanOrEqual(8);
});

it("deletes a duty through stop only after a second confirmation", async () => {
  client.dutySchedules.mockResolvedValue([duty]);
  assistantRequest.mockResolvedValue({ status: "cancelled", revision: 3 });
  await render();

  await click(byLabel("每日巡检 的更多操作"));
  await click(byText("删除"));
  expect(host.textContent).toContain("每日巡检");
  expect(buttons().some(byText("确认删除？"))).toBe(true);

  await click(byText("确认删除？"));
  expect(assistantRequest).toHaveBeenCalledWith(
    "objectives/duty_1/actions",
    "POST",
    expect.objectContaining({ action: "stop", expectedRevision: 2 }),
  );
  expect(client.remove).not.toHaveBeenCalled();
  expect(host.textContent).not.toContain("每日巡检");
});

it("maps automation card actions to pause, enable and run", async () => {
  client.list.mockResolvedValue([task]);
  client.pause.mockResolvedValue({ ...task, status: "paused" });
  client.enable.mockResolvedValue({ ...task, status: "active" });
  client.run.mockResolvedValue({});
  await render();

  expect(switchState("每周工作周报")).toBe("true");
  await click(switchFor("每周工作周报"));
  expect(client.pause).toHaveBeenCalledWith("task_1");
  expect(host.textContent).toContain("已暂停");
  expect(switchState("每周工作周报")).toBe("false");

  await click(switchFor("每周工作周报"));
  expect(client.enable).toHaveBeenCalledWith("task_1");
  expect(host.textContent).toContain("已启用");
  expect(switchState("每周工作周报")).toBe("true");

  await click(byLabel("立即执行 每周工作周报"));
  expect(client.run).toHaveBeenCalledWith("task_1");
});

it("keeps the strip icon-only and wires the more menu to its trigger", async () => {
  client.dutySchedules.mockResolvedValue([duty]);
  await render();

  expect(host.textContent).not.toContain("更多 ▾");
  const more = buttons().find(byLabel("每日巡检 的更多操作"));
  expect(more?.getAttribute("aria-haspopup")).toBe("menu");
  expect(more?.getAttribute("aria-expanded")).toBe("false");

  await click(byLabel("每日巡检 的更多操作"));
  expect(buttons().find(byLabel("每日巡检 的更多操作"))?.getAttribute("aria-expanded")).toBe("true");
  expect(buttons().some(byText("编辑"))).toBe(true);
});

it("shows the run button busy while a duty execution is in flight", async () => {
  client.dutySchedules.mockResolvedValue([duty]);
  assistantRequest.mockImplementation(() => new Promise(() => undefined));
  await render();

  const run = buttons().find(byLabel("立即执行 每日巡检"));
  if (!run) throw new Error("run button missing");
  await act(async () => { run.click(); });
  await flush();

  const busy = buttons().find(byLabel("立即执行 每日巡检"));
  expect(busy?.getAttribute("aria-busy")).toBe("true");
  expect(busy?.hasAttribute("disabled")).toBe(true);
});

it("deletes an automation task with DELETE after a second confirmation", async () => {
  // Avoid a name that also appears in the empty-state templates.
  client.list.mockResolvedValue([{ ...task, name: "我的周报任务" }]);
  client.remove.mockResolvedValue(undefined);
  await render();

  await click(byLabel("我的周报任务 的更多操作"));
  await click(byText("删除"));
  expect(host.textContent).toContain("我的周报任务");

  await click(byText("确认删除？"));
  expect(client.remove).toHaveBeenCalledWith("task_1");
  expect(host.textContent).not.toContain("我的周报任务");
});

it("hides ended schedules (completed, cancelled, expired) from the default list", async () => {
  client.dutySchedules.mockResolvedValue([
    { ...duty, sourceId: "duty_done", title: "已完成的巡检", status: "completed" },
    { ...duty, sourceId: "duty_stopped", title: "已取消的巡检", status: "cancelled" },
  ]);
  client.list.mockResolvedValue([{ ...task, taskId: "task_expired", name: "过期的周报", status: "expired" }]);
  await render();

  expect(host.textContent).not.toContain("已完成的巡检");
  expect(host.textContent).not.toContain("已取消的巡检");
  expect(host.textContent).not.toContain("过期的周报");
  expect(host.textContent).toContain("自动化任务模版");
});

it("shows templates only while the visible list is empty", async () => {
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
