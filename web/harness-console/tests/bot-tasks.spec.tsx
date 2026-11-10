// @vitest-environment jsdom
import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
const request = vi.fn();
vi.mock("../src/lib/assistant-client", () => ({ assistantRequest: (...args: unknown[]) => request(...args) }));
import { BotTasks, DutyEditDialog } from "../src/components/bot-tasks";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
const host = document.createElement("div");
let root = createRoot(host);
afterEach(() => { act(() => root.unmount()); host.replaceChildren(); root = createRoot(host); request.mockReset(); });

const duty = {
  objectiveId: "coding", title: "检查改动", revision: 3, status: "active",
  schedule: { kind: "cron" as const, expression: "0 9 * * 5", timezone: "Asia/Shanghai" },
  nextCheckAt: "2026-11-13T01:00:00Z",
};

function objective(overrides: Record<string, unknown> = {}) {
  return {
    objectiveId: "coding", title: "检查改动", status: "active", revision: 3,
    instructions: "看今天的改动", agentName: "assistant", agentVersion: "v1",
    notificationMode: "important", budget: null, endsAt: null,
    schedule: duty.schedule, nextCheckAt: duty.nextCheckAt, ...overrides,
  };
}

async function render(node: ReactNode) {
  await act(async () => { root.render(node); await Promise.resolve(); });
}

async function type(field: HTMLInputElement | HTMLTextAreaElement, value: string) {
  await act(async () => {
    const prototype = field instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(prototype, "value")!.set!.call(field, value);
    field.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function byText(text: string) {
  return [...host.querySelectorAll("button")].find(item => item.textContent?.includes(text));
}

it("lists the Bot's duties with their schedule and opens one for editing", async () => {
  request.mockImplementation(async (path: string) => path === "specialists/a"
    ? { duties: [duty] } : objective());
  await render(<BotTasks specialistId="a" />);
  expect(host.textContent).toContain("定时任务");
  expect(host.textContent).toContain("每周五 09:00");
  expect(host.textContent).toContain("进行中");
  await act(async () => byText("检查改动")!.click());
  expect(host.textContent).toContain("编辑定时任务");
  expect(host.textContent).toContain("看今天的改动");
  expect((host.querySelector('input[maxlength="40"]') as HTMLInputElement).value).toBe("检查改动");
});

it("saves name, requirements and repeat in one brief update", async () => {
  const onSaved = vi.fn();
  request.mockImplementation(async (path: string, method?: string) => method === "PUT"
    ? objective({ revision: 4, title: "每周体检改动" })
    : objective());
  await render(<DutyEditDialog duty={duty} onClose={vi.fn()} onSaved={onSaved} />);

  await type(host.querySelector('input[maxlength="40"]') as HTMLInputElement, "每周体检改动");
  await type(host.querySelector("textarea")!, "看所有仓库的改动");
  const repeat = [...host.querySelectorAll("select")]
    .find(item => item.getAttribute("aria-label") === "检查改动的重复方式")!;
  await act(async () => { repeat.value = "monthly"; repeat.dispatchEvent(new Event("change", { bubbles: true })); });
  await type(host.querySelector('input[aria-label="日"]') as HTMLInputElement, "15");
  await act(async () => byText("保存")!.click());

  expect(request).toHaveBeenCalledWith("objectives/coding", "PUT", expect.objectContaining({
    title: "每周体检改动",
    instructions: "看所有仓库的改动",
    agentName: "assistant",
    agentVersion: "v1",
    schedule: { kind: "cron", expression: "0 9 15 * *", timezone: "Asia/Shanghai" },
    expectedRevision: 3,
  }));
  expect(onSaved).toHaveBeenCalledOnce();
});

it("deletes by stopping the Objective after a confirmation", async () => {
  const onSaved = vi.fn();
  const onClose = vi.fn();
  request.mockImplementation(async (path: string, method?: string) => method === "POST"
    ? { ...duty, status: "cancelled", revision: 4 }
    : objective());
  await render(<DutyEditDialog duty={duty} onClose={onClose} onSaved={onSaved} />);

  await act(async () => byText("删除任务")!.click());
  expect(request).not.toHaveBeenCalledWith("objectives/coding/actions", "POST", expect.anything());
  await act(async () => byText("确认删除")!.click());
  expect(request).toHaveBeenCalledWith("objectives/coding/actions", "POST", {
    action: "stop", expectedRevision: 3,
  });
  expect(onSaved).toHaveBeenCalledWith(expect.objectContaining({ status: "cancelled" }));
  expect(onClose).toHaveBeenCalled();
});

it("keeps read-only tasks from offering edits", async () => {
  request.mockImplementation(async () => objective({ status: "cancelled" }));
  await render(<DutyEditDialog duty={{ ...duty, status: "cancelled" }} onClose={vi.fn()} onSaved={vi.fn()} />);
  expect((host.querySelector('input[maxlength="40"]') as HTMLInputElement).disabled).toBe(true);
  expect(byText("删除任务")).toBeUndefined();
  expect(byText("保存")!.disabled).toBe(true);
});
