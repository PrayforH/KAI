// @vitest-environment jsdom
import { act, type ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
const request = vi.fn();
vi.mock("../src/lib/assistant-client", () => ({ assistantRequest: (...args: unknown[]) => request(...args) }));
import type { Execution } from "../src/lib/assistant-client";
import { DutyRunHistory, filterExecutions, triggerKind } from "../src/components/duty-run-history";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
const host = document.createElement("div");
let root = createRoot(host);
afterEach(() => { act(() => root.unmount()); host.replaceChildren(); root = createRoot(host); request.mockReset(); });

function run(index: number, overrides: Partial<Execution> = {}): Execution {
  return {
    executionId: `execution_${index}`,
    threadId: "thread_1",
    runId: `run_${index}`,
    status: "succeeded",
    summary: `第 ${index} 次`,
    createdAt: `2026-11-0${(index % 9) + 1}T09:00:00Z`,
    triggerKey: "schedule:cron",
    ...overrides,
  };
}

async function render(node: ReactNode) {
  await act(async () => { root.render(node); await Promise.resolve(); });
}

async function choose(label: string, value: string) {
  await act(async () => {
    const field = [...host.querySelectorAll("select")]
      .find(item => item.getAttribute("aria-label") === label)!;
    field.value = value;
    field.dispatchEvent(new Event("change", { bubbles: true }));
  });
}

it("labels trigger namespaces instead of dumping the raw key", () => {
  expect(triggerKind("schedule:cron:0 9 * * *")).toBe("schedule");
  expect(triggerKind("manual:abc")).toBe("manual");
  expect(triggerKind("delegate:run:objective")).toBe("delegate");
  expect(triggerKind("delegate-specialist:message:objective")).toBe("delegate");
  expect(triggerKind("manage:run:objective")).toBe("manage");
  expect(triggerKind("trial:specialist_1:2")).toBe("trial");
  expect(triggerKind("feishu:digest")).toBe("feishu");
  expect(triggerKind(undefined)).toBe("other");
});

it("filters by status and trigger", () => {
  const runs = [
    run(1),
    run(2, { status: "failed", triggerKey: "manual:x" }),
    run(3, { status: "failed", triggerKey: "delegate:r:o" }),
  ];
  expect(filterExecutions(runs, { status: "failed", trigger: "all" }).map(item => item.executionId))
    .toEqual(["execution_2", "execution_3"]);
  expect(filterExecutions(runs, { status: "all", trigger: "delegate" }).map(item => item.executionId))
    .toEqual(["execution_3"]);
  expect(filterExecutions(runs, { status: "succeeded", trigger: "schedule" }).map(item => item.executionId))
    .toEqual(["execution_1"]);
});

it("shows a page of runs, filters them, and links each to its conversation", async () => {
  request.mockImplementation(async () => Array.from({ length: 12 }, (_, index) => run(index)));
  await render(<DutyRunHistory objectiveId="coding" title="检查改动" scheduleText="每周五 09:00" onBack={vi.fn()} />);
  expect(host.textContent).toContain("共 12 条 · 第 1/2 页");
  expect(host.querySelectorAll("a[href^='/?thread=']")).toHaveLength(10);
  const first = host.querySelector("a[href^='/?thread=']") as HTMLAnchorElement;
  expect(first.getAttribute("href")).toBe("/?thread=thread_1&run=run_0");

  await act(async () => [...host.querySelectorAll("button")].find(item => item.textContent === "下一页")!.click());
  expect(host.textContent).toContain("第 2/2 页");
  expect(host.querySelectorAll("a[href^='/?thread=']")).toHaveLength(2);

  await choose("按状态筛选", "failed");
  expect(host.textContent).toContain("没有符合筛选条件的记录");
});

it("says so when the server's 100-row cap is reached", async () => {
  request.mockImplementation(async () => Array.from({ length: 100 }, (_, index) => run(index)));
  await render(<DutyRunHistory objectiveId="coding" title="检查改动" scheduleText="每天 09:00" onBack={vi.fn()} />);
  expect(host.textContent).toContain("服务端一次最多返回最近 100 条执行");
});

it("goes back to the task list", async () => {
  request.mockImplementation(async () => []);
  const back = vi.fn();
  await render(<DutyRunHistory objectiveId="coding" title="检查改动" scheduleText="每天 09:00" onBack={back} />);
  expect(host.textContent).toContain("暂无执行记录");
  await act(async () => [...host.querySelectorAll("button")].find(item => item.textContent === "返回")!.click());
  expect(back).toHaveBeenCalled();
});
