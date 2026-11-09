// @vitest-environment jsdom
import { act } from "react";
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

it("saves one duty's explicit interval with CAS and does not start an execution", async () => {
  const onSaved=vi.fn();
  request.mockResolvedValue({...duty, revision:4, schedule:{intervalMinutes:60, timezone:"Asia/Shanghai"}});
  await act(async()=> root.render(<DutyScheduleControl duty={duty} onSaved={onSaved}/>));
  expect(host.querySelector("select")?.value).toBe("");
  await act(async()=> { const field=host.querySelector("select")!; field.value="60";field.dispatchEvent(new Event("change",{bubbles:true})); });
  await act(async()=> (host.querySelector("button") as HTMLButtonElement).click());
  expect(request).toHaveBeenCalledExactlyOnceWith("objectives/coding/schedule","PATCH",{
    expectedRevision:3,schedule:{intervalMinutes:60,timezone:"Asia/Shanghai"},
  });
  expect(onSaved).toHaveBeenCalledOnce();
  expect(host.querySelector('[role="status"]')?.textContent).toContain("已保存");
});

it("clears only this duty's existing rule while retaining errors for a stale revision", async () => {
  const onSaved=vi.fn(); request.mockRejectedValue(new Error("任务已修改，请刷新"));
  await act(async()=> root.render(<DutyScheduleControl duty={{...duty,schedule:{intervalMinutes:75,timezone:"UTC"}}} onSaved={onSaved}/>));
  expect(host.querySelector("select")?.value).toBe("75");
  await act(async()=> { const field=host.querySelector("select")!;field.value="";field.dispatchEvent(new Event("change",{bubbles:true})); });
  await act(async()=> (host.querySelector("button") as HTMLButtonElement).click());
  expect(request).toHaveBeenCalledExactlyOnceWith("objectives/coding/schedule","PATCH",{expectedRevision:3,schedule:null});
  expect(onSaved).not.toHaveBeenCalled();
  expect(host.querySelector('[role="alert"]')?.textContent).toContain("请刷新");
  expect((host.querySelector("button") as HTMLButtonElement).disabled).toBe(false);
});

it("opens the current Bot's plans and sends pause to its Objective", async () => {
  const plan = { ...duty, schedule: { kind: "cron", expression: "0 9 * * 1-5", timezone: "UTC" } };
  request.mockResolvedValueOnce({
    duties: [{ ...plan, latestExecution: { status: "succeeded", summary: "已生成报告" } }],
  }).mockResolvedValueOnce({ ...plan, status: "paused", revision: 4 });
  await act(async () => root.render(<BotDuties specialistId="specialist_a" />));
  expect(request).toHaveBeenNthCalledWith(1, "specialists/specialist_a");
  expect(host.querySelector("select")?.value).toBe("cron");
  expect(host.textContent).toContain("已生成报告");
  const pause = [...host.querySelectorAll("button")].find(item => item.textContent === "暂停职责");
  await act(async () => pause?.click());
  expect(request).toHaveBeenNthCalledWith(2, "objectives/coding/actions", "POST", {
    action: "pause", expectedRevision: 3,
  });
  expect(host.textContent).toContain("恢复职责");
});
