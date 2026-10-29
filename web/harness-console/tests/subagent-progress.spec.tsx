// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it } from "vitest";
import { SubagentProgress } from "../src/components/subagent-progress";
import type { RunTaskNode } from "../src/lib/run-view-model";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

it("keeps explicit folding across progress updates and exposes safe results", async () => {
  const element = document.createElement("div"); const root = createRoot(element);
  const tasks: RunTaskNode[] = [
    { id: "a", title: "检查材料", alias: "researcher", status: "running", sequence: 1 },
    { id: "b", title: "核算利润", alias: "analyst", status: "running", sequence: 2 },
  ];
  try {
    await act(async () => root.render(<SubagentProgress tasks={tasks} active />));
    expect(element.textContent).toContain("2 个执行中");
    expect(element.querySelectorAll("li")).toHaveLength(2);
    await act(async () => (element.querySelector("button") as HTMLButtonElement).click());
    expect(element.querySelectorAll("li")).toHaveLength(0);
    const finished = tasks.map(task => ({ ...task, status: "completed" as const, result: "结果 <script>alert(1)</script>", durationMs: 5300 }));
    await act(async () => root.render(<SubagentProgress tasks={finished} active={false} />));
    expect(element.textContent).toContain("2/2 已完成");
    expect(element.querySelector("button")?.getAttribute("aria-expanded")).toBe("false");
    await act(async () => (element.querySelector("button") as HTMLButtonElement).click());
    expect(element.textContent).toContain("资料研究");
    expect(element.textContent).toContain("返回结果");
    expect(element.querySelector("script")).toBeNull();
    expect(element.textContent).toContain("5秒");
  } finally { await act(async () => root.unmount()); }
});

it("shows unfinished tasks as stopped after cancellation", async () => {
  const element = document.createElement("div"); const root = createRoot(element);
  try {
    await act(async () => root.render(<SubagentProgress active={false} tasks={[
      { id: "x", title: "检查", alias: "reviewer", status: "failed", errorCode: "cancelled", sequence: 1 },
    ]} />));
    expect(element.textContent).toContain("已停止");
    expect(element.textContent).not.toContain("1/1 已完成");
  } finally { await act(async () => root.unmount()); }
});
