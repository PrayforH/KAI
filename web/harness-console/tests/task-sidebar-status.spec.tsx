// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TaskSidebar } from "../src/components/task-sidebar";
import { activityStore } from "../src/lib/activity-store";
import { runActivitySchema } from "../src/lib/activity-schema";
import type { TaskSummary } from "../src/lib/task-history";

const { loadTasks, markTaskRead } = vi.hoisted(() => ({ loadTasks: vi.fn(), markTaskRead: vi.fn() }));
vi.mock("../src/lib/task-history", () => ({ loadTasks, markTaskRead, isTaskRead: (task: TaskSummary) => Boolean(task.last_read_at && Date.parse(task.last_read_at) >= Date.parse(task.updated_at)), prefetchThreadHistory: vi.fn().mockResolvedValue(undefined), setTaskArchived: vi.fn() }));
vi.mock("../src/components/auth-provider", () => ({ useAuth: () => ({ user: { user_id: "test" } }) }));
vi.mock("../src/components/account-menu", () => ({ AccountMenu: () => null }));
vi.mock("../src/components/workspace-navigation", () => ({ WorkspaceNavigation: () => null }));
vi.mock("../src/components/product-brand", () => ({ ProductBrandMark: () => null, PRODUCT_NAME: "KAI" }));
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function task(status: string, id = status): TaskSummary {
  return { thread_id: id, run_id: `run-${id}`, session_id: id, title: `任务 ${id}`,
    agent_name: "test", agent_version: "1", agent_owner_user_id: "test", status,
    created_at: "2026-09-07T00:00:00Z", updated_at: "2026-09-07T00:00:00Z" };
}
beforeEach(() => {
  markTaskRead.mockImplementation(async (_id: string, updatedAt: string) => updatedAt);
  const values = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  });
});
afterEach(() => { activityStore.clear(); vi.clearAllMocks(); vi.unstubAllGlobals(); });

it("keeps errors visible for opened and background tasks with accessible status text", async () => {
  loadTasks.mockResolvedValue([task("failed"), task("timed_out"), task("rejected"), task("succeeded")]);
  const container = document.createElement("div"); document.body.appendChild(container);
  const root = createRoot(container);
  try {
    await act(async () => root.render(<TaskSidebar currentThreadId="failed" collapsed={false} onToggle={() => {}} onSelect={() => {}} onNewTask={() => {}} />));
    expect([...container.querySelectorAll(".task-error-label")].map((node) => node.textContent)).toEqual(["处理错误", "已超时", "已拒绝"]);
    expect(container.querySelector('[aria-label="任务 failed，处理错误"]')).not.toBeNull();
    expect(container.querySelector(".task-status.status-failed")).not.toBeNull();
    expect(container.querySelector(".status-unread")).not.toBeNull();
  } finally { await act(async () => root.unmount()); container.remove(); }
});

it("shows live failure immediately and does not apply an older run to a retried task", async () => {
  loadTasks.mockResolvedValue([task("running", "current")]);
  const container = document.createElement("div"); document.body.appendChild(container);
  const root = createRoot(container);
  const render = () => root.render(<TaskSidebar currentThreadId="current" collapsed={false} onToggle={() => {}} onSelect={() => {}} onNewTask={() => {}} />);
  try {
    await act(async () => render());
    await act(async () => activityStore.publish(runActivitySchema.parse({
      run_id: "run-current", status: "failed", started_at: "2026-09-07T00:00:00Z", items: [{
        id: "error", event_type: "run.failed", kind: "error", status: "failed", title: "执行失败",
        timestamp: "2026-09-07T00:00:10Z", sequence: 1,
      }],
    })));
    expect(container.querySelector(".task-error-label")?.textContent).toBe("处理错误");
    loadTasks.mockResolvedValue([{ ...task("running", "current"), run_id: "run-retry" }]);
    await act(async () => window.dispatchEvent(new Event("focus")));
    expect(container.querySelector(".task-error-label")).toBeNull();
    expect(container.querySelector(".status-running")).not.toBeNull();
  } finally { await act(async () => root.unmount()); container.remove(); }
});

it("collapses each group's tasks after five and expands/collapses independently", async () => {
  loadTasks.mockResolvedValue(Array.from({ length: 7 }, (_, i) => task("succeeded", String(i))));
  const container = document.createElement("div"); document.body.appendChild(container);
  const root = createRoot(container);
  try {
    await act(async () => root.render(<TaskSidebar currentThreadId="" collapsed={false} onToggle={() => {}} onSelect={() => {}} onNewTask={() => {}} />));
    expect(container.querySelectorAll(".task-list-row")).toHaveLength(5);
    const toggle = container.querySelector<HTMLButtonElement>(".tasks-show-more")!;
    expect(toggle.textContent).toBe("展开显示（2）");
    await act(async () => toggle.click());
    expect(container.querySelectorAll(".task-list-row")).toHaveLength(7);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    await act(async () => toggle.click());
    expect(container.querySelectorAll(".task-list-row")).toHaveLength(5);
  } finally { await act(async () => root.unmount()); container.remove(); }
});

it("uses server read state on a fresh device and notices later results", async () => {
  const read = { ...task("succeeded", "remote"), last_read_at: "2026-09-07T00:00:00+00:00" };
  loadTasks.mockResolvedValue([read]);
  const container = document.createElement("div"); document.body.appendChild(container);
  const root = createRoot(container);
  try {
    await act(async () => root.render(<TaskSidebar currentThreadId="" collapsed={false} onToggle={() => {}} onSelect={() => {}} onNewTask={() => {}} />));
    expect(container.querySelector(".status-unread")).toBeNull();
    expect(markTaskRead).not.toHaveBeenCalled();
    loadTasks.mockResolvedValue([{ ...read, updated_at: "2026-09-07T00:01:00Z" }]);
    await act(async () => window.dispatchEvent(new Event("focus")));
    expect(container.querySelector(".status-unread")).not.toBeNull();
    await act(async () => root.render(<TaskSidebar currentThreadId="remote" collapsed={false} onToggle={() => {}} onSelect={() => {}} onNewTask={() => {}} />));
    expect(markTaskRead).toHaveBeenCalledWith("remote", "2026-09-07T00:01:00Z");
  } finally { await act(async () => root.unmount()); container.remove(); }
});
