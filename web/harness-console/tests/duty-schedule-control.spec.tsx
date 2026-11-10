// @vitest-environment jsdom
import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
const request = vi.fn();
vi.mock("../src/lib/assistant-client", () => ({ assistantRequest: (...args: unknown[]) => request(...args) }));
import { DutyScheduleControl, type BotDuty } from "../src/components/duty-schedule-control";
import { BotDuties } from "../src/components/bot-duties-dialog";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
const host = document.createElement("div");
let root = createRoot(host);
afterEach(() => { act(() => root.unmount()); host.replaceChildren(); root=createRoot(host); request.mockReset(); });
const duty: BotDuty = {objectiveId:"coding", title:"检查改动", revision:3, status:"active", schedule:null};

/** The control reads the objective once for its validity window. */
function objective(overrides: Record<string, unknown> = {}) {
  return {
    objectiveId: "coding", title: "检查改动", status: "active", revision: 3,
    instructions: "看改动", agentName: "assistant", agentVersion: "v1",
    completionCriteria: "", notificationMode: "important", budget: null,
    endsAt: null, schedule: null, ...overrides,
  };
}

async function render(node: ReactNode) {
  await act(async () => { root.render(node); await Promise.resolve(); });
}

function select(label: RegExp) {
  return [...host.querySelectorAll("select")]
    .find(item => label.test(item.getAttribute("aria-label") ?? ""))!;
}

async function choose(label: RegExp, value: string) {
  await act(async () => {
    const field = select(label);
    field.value = value;
    field.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

/** React tracks input values through the native setter, so a plain assignment
 *  never reaches onChange. Write through the prototype and fire `input`. */
async function type(field: HTMLInputElement, value: string) {
  await act(async () => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(field, value);
    field.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

it("saves one duty's explicit interval with CAS and does not start an execution", async () => {
  const onSaved=vi.fn();
  request.mockImplementation(async (path: string) => path === "objectives/coding"
    ? objective({ revision: 4, schedule: { intervalMinutes: 60, timezone: "Asia/Shanghai" } })
    : { ...duty, revision:4, schedule:{intervalMinutes:60, timezone:"Asia/Shanghai"} });
  await render(<DutyScheduleControl duty={duty} onSaved={onSaved}/>);
  expect(select(/重复方式/).value).toBe("");
  await choose(/重复方式/, "60");
  await act(async()=> (host.querySelector("button") as HTMLButtonElement).click());
  expect(request).toHaveBeenCalledWith("objectives/coding/schedule","PATCH",{
    expectedRevision:3,schedule:{intervalMinutes:60,timezone:"Asia/Shanghai"},
  });
  expect(onSaved).toHaveBeenCalledOnce();
  expect(host.querySelector('[role="status"]')?.textContent).toContain("已保存");
});

it("turns the prototype's repeats into the cron the server stores", async () => {
  request.mockImplementation(async () => objective());
  await render(<DutyScheduleControl duty={duty} onSaved={vi.fn()}/>);

  await choose(/重复方式/, "daily");
  await act(async()=> (host.querySelector("button") as HTMLButtonElement).click());
  expect(request).toHaveBeenLastCalledWith("objectives/coding/schedule","PATCH",{
    expectedRevision:3,schedule:{kind:"cron",expression:"0 9 * * *",timezone:"Asia/Shanghai"},
  });

  await choose(/重复方式/, "monthly");
  await type(host.querySelector('input[aria-label="日"]') as HTMLInputElement, "15");
  await act(async()=> (host.querySelector("button") as HTMLButtonElement).click());
  expect(request).toHaveBeenLastCalledWith("objectives/coding/schedule","PATCH",{
    expectedRevision:3,schedule:{kind:"cron",expression:"0 9 15 * *",timezone:"Asia/Shanghai"},
  });
});

it("reads a monthly cron back into the same form", async () => {
  const monthly: BotDuty = {
    ...duty, schedule: { kind: "cron", expression: "30 8 15 * *", timezone: "Asia/Shanghai" },
  };
  request.mockImplementation(async () => objective({ schedule: monthly.schedule }));
  await render(<DutyScheduleControl duty={monthly} onSaved={vi.fn()}/>);
  expect(select(/重复方式/).value).toBe("monthly");
  expect((host.querySelector('input[aria-label="日"]') as HTMLInputElement).value).toBe("15");
  expect((host.querySelector('input[type="time"]') as HTMLInputElement).value).toBe("08:30");
  // Nothing changed, so the save button stays disabled.
  expect((host.querySelector("button") as HTMLButtonElement).disabled).toBe(true);
});

it("writes the validity window through the objective brief and keeps the rest intact", async () => {
  const onSaved = vi.fn();
  request.mockImplementation(async (path: string, method?: string) => {
    if (method === "PUT") return objective({ revision: 4, endsAt: "2026-12-31T15:59:59Z" });
    return objective();
  });
  await render(<DutyScheduleControl duty={duty} onSaved={onSaved}/>);

  await choose(/有效期/, "until");
  await type(host.querySelector('input[type="date"]') as HTMLInputElement, "2026-12-31");
  await act(async()=> (host.querySelector("button") as HTMLButtonElement).click());
  const [, method, body] = request.mock.calls.find(([, verb]) => verb === "PUT")!;
  expect(method).toBe("PUT");
  expect(body).toMatchObject({
    title: "检查改动",
    instructions: "看改动",
    agentName: "assistant",
    agentVersion: "v1",
    expectedRevision: 3,
    endsAt: expect.stringContaining("2026-12-31"),
  });
  expect(onSaved).toHaveBeenCalledOnce();
});

it("clears only this duty's existing rule while retaining errors for a stale revision", async () => {
  const onSaved=vi.fn();
  request.mockImplementation(async (path: string) =>
    path === "objectives/coding" ? objective({ schedule: { intervalMinutes: 75, timezone: "UTC" } }) : Promise.reject(new Error("任务已修改，请刷新")));
  const timed = { ...duty, schedule: { intervalMinutes: 75, timezone: "UTC" } };
  await render(<DutyScheduleControl duty={timed} onSaved={onSaved}/>);
  expect(select(/重复方式/).value).toBe("75");
  await choose(/重复方式/, "");
  await act(async()=> (host.querySelector("button") as HTMLButtonElement).click());
  expect(request).toHaveBeenCalledWith("objectives/coding/schedule","PATCH",{expectedRevision:3,schedule:null});
  expect(onSaved).not.toHaveBeenCalled();
  expect(host.querySelector('[role="alert"]')?.textContent).toContain("请刷新");
  expect((host.querySelector("button") as HTMLButtonElement).disabled).toBe(false);
});

it("shows the saved rule in one line in the duty list", async () => {
  const plan = { ...duty, schedule: { kind: "cron", expression: "0 9 * * 5", timezone: "Asia/Shanghai" } };
  request.mockImplementation(async (path: string) => path.startsWith("specialists/")
    ? { duties: [plan] }
    : objective());
  await render(<BotDuties specialistId="specialist_a" />);
  expect(host.textContent).toContain("每周五 09:00");
});

it("opens the current Bot's plans and sends pause to its Objective", async () => {
  const plan = { ...duty, schedule: { kind: "cron", expression: "0 9 * * 1-5", timezone: "UTC" } };
  request.mockImplementation(async (path: string) => {
    if (path.startsWith("specialists/")) return { duties: [{ ...plan, latestExecution: { status: "succeeded", summary: "已生成报告" } }] };
    if (path === "objectives/coding/actions") return { ...plan, status: "paused", revision: 4 };
    return objective({ schedule: plan.schedule });
  });
  await render(<BotDuties specialistId="specialist_a" />);
  expect(request).toHaveBeenCalledWith("specialists/specialist_a");
  expect(select(/重复方式/).value).toBe("cron");
  expect(host.textContent).toContain("已生成报告");
  const pause = [...host.querySelectorAll("button")].find(item => item.textContent === "暂停职责");
  await act(async () => pause?.click());
  expect(request).toHaveBeenCalledWith("objectives/coding/actions", "POST", {
    action: "pause", expectedRevision: 3,
  });
  expect(host.textContent).toContain("恢复职责");
});
