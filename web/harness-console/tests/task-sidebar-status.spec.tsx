// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TaskSidebar } from "../src/components/task-sidebar";
import { activityStore } from "../src/lib/activity-store";
import { runActivitySchema } from "../src/lib/activity-schema";
import type { TaskSummary } from "../src/lib/task-history";

const { loadTasks, markTaskRead, setTaskPinned } = vi.hoisted(() => ({ loadTasks: vi.fn(), markTaskRead: vi.fn(), setTaskPinned: vi.fn() }));
vi.mock("../src/lib/task-history", () => ({ loadTasks, markTaskRead, isTaskRead: (task: TaskSummary) => Boolean(task.last_read_at && Date.parse(task.last_read_at) >= Date.parse(task.updated_at)), prefetchThreadHistory: vi.fn().mockResolvedValue(undefined), setTaskArchived: vi.fn(), setTaskPinned, setTaskTitle: vi.fn(), forkTask: vi.fn(), peekCachedTasks: vi.fn(() => null), notifyTaskListChanged: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("../src/components/auth-provider", () => ({ useAuth: () => ({ user: { user_id: "test" } }) }));
vi.mock("../src/components/account-menu", () => ({ AccountMenu: () => null }));
vi.mock("../src/components/workspace-navigation", () => ({ WorkspaceNavigation: () => null }));
vi.mock("../src/components/product-brand", () => ({ ProductBrandMark: () => null, ProductWordmark: () => <strong>KAI</strong>, PRODUCT_NAME: "KAI" }));
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

it("creates from an empty project's plus, prevents duplicate clicks, and expands it", async () => {
  loadTasks.mockResolvedValue([]);
  let finish!: () => void;
  const create = vi.fn(() => new Promise<void>((resolve) => { finish = resolve; }));
  const project = { projectId: "empty-project", name: "空项目", tenantId: "test", userId: "test", createdAt: "2026-09-20T00:00:00Z", updatedAt: "2026-09-20T00:00:00Z" };
  const container = document.createElement("div"); document.body.appendChild(container);
  const root = createRoot(container);
  try {
    await act(async () => root.render(<TaskSidebar currentThreadId="" collapsed={false} onToggle={() => {}} onSelect={() => {}} onNewTask={() => {}} projects={[project]} onNewTaskWithProject={create} />));
    const button = container.querySelector<HTMLButtonElement>('[aria-label="在 空项目 下新建任务"]')!;
    expect(button).not.toBeNull();
    await act(async () => { button.click(); button.click(); });
    expect(create).toHaveBeenCalledExactlyOnceWith(project);
    expect(button.disabled).toBe(true);
    await act(async () => finish());
    expect(button.disabled).toBe(false);
    expect(container.querySelector('[aria-label="收起项目 空项目"]')).not.toBeNull();
  } finally { await act(async () => root.unmount()); container.remove(); }
});

it("keeps project creation failures visible and allows retry", async () => {
  loadTasks.mockResolvedValue([]);
  const create = vi.fn().mockRejectedValue(new Error("新建任务失败，请稍后重试"));
  const project = { projectId: "project", name: "项目", tenantId: "test", userId: "test", createdAt: "", updatedAt: "" };
  const container = document.createElement("div"); document.body.appendChild(container);
  const root = createRoot(container);
  try {
    await act(async () => root.render(<TaskSidebar currentThreadId="" collapsed={false} onToggle={() => {}} onSelect={() => {}} onNewTask={() => {}} projects={[project]} onNewTaskWithProject={create} />));
    const button = container.querySelector<HTMLButtonElement>('[aria-label="在 项目 下新建任务"]')!;
    await act(async () => button.click());
    expect(container.querySelector('[role="alert"]')?.textContent).toBe("新建任务失败，请稍后重试");
    expect(button.disabled).toBe(false);
    await act(async () => button.click());
    expect(create).toHaveBeenCalledTimes(2);
  } finally { await act(async () => root.unmount()); container.remove(); }
});

it("preserves project expansion and show-more choices when workspace navigation remounts the sidebar", async () => {
  loadTasks.mockResolvedValue(Array.from({ length: 7 }, (_, i) => ({ ...task("succeeded", String(i)), project_id: "project-a" })));
  const props = { currentThreadId: "", collapsed: false, onToggle() {}, onSelect() {}, onNewTask() {}, projects: [{ projectId: "project-a", name: "研究", userId: "test", tenantId: "local", createdAt: "", updatedAt: "" }] };
  const container = document.createElement("div"); document.body.appendChild(container);
  let root = createRoot(container);
  try {
    await act(async () => root.render(<TaskSidebar {...props} />));
    await act(async () => container.querySelector<HTMLButtonElement>('[aria-label="展开项目 研究"]')!.click());
    await act(async () => container.querySelector<HTMLButtonElement>('.tasks-show-more')!.click());
    expect(container.querySelectorAll('.task-list-row')).toHaveLength(7);
    await act(async () => root.unmount());
    root = createRoot(container);
    await act(async () => root.render(<TaskSidebar {...props} activeNav="skills" />));
    expect(container.querySelector('[aria-label="收起项目 研究"]')).not.toBeNull();
    expect(container.querySelectorAll('.task-list-row')).toHaveLength(7);
  } finally { await act(async () => root.unmount()); container.remove(); }
});

it("preserves a deliberately collapsed current project across remounts", async () => {
  loadTasks.mockResolvedValue([{ ...task("succeeded", "selected"), project_id: "project-a" }]);
  const props = { currentThreadId: "selected", collapsed: false, onToggle() {}, onSelect() {}, onNewTask() {}, projects: [{ projectId: "project-a", name: "研究", userId: "test", tenantId: "local", createdAt: "", updatedAt: "" }] };
  const container = document.createElement("div"); let root = createRoot(container);
  try {
    await act(async () => root.render(<TaskSidebar {...props} />));
    await act(async () => container.querySelector<HTMLButtonElement>('[aria-label="收起项目 研究"]')!.click());
    await act(async () => root.unmount()); root = createRoot(container);
    await act(async () => root.render(<TaskSidebar {...props} />));
    expect(container.querySelector('[aria-label="展开项目 研究"]')).not.toBeNull();
  } finally { await act(async () => root.unmount()); }
});


it("collapses project and task sections independently and persists them across remounts", async () => {
  const projects = [{ projectId: "p1", tenantId: "test", userId: "test", name: "示例项目", description: "", createdAt: "2026-09-07T00:00:00Z", updatedAt: "2026-09-07T00:00:00Z" }];
  loadTasks.mockResolvedValue([{ ...task("succeeded", "project-task"), project_id: "p1" },
    ...Array.from({ length: 7 }, (_, i) => task("succeeded", `loose-${i}`))]);
  const container = document.createElement("div"); document.body.appendChild(container);
  let root = createRoot(container);
  const render = () => root.render(<TaskSidebar currentThreadId="project-task" collapsed={false} projects={projects}
    onToggle={() => {}} onSelect={() => {}} onNewTask={() => {}} />);
  const button = (label: string) => container.querySelector<HTMLButtonElement>(`[aria-label="${label}"]`)!;
  try {
    await act(async () => render());
    expect([...container.querySelectorAll(".task-section-toggle")].map((item) => item.textContent)).toEqual(["项目", "任务"]);
    expect(container.querySelectorAll(".task-list-row")).toHaveLength(6);
    await act(async () => button("展开任务").click());
    expect(container.querySelectorAll(".task-list-row")).toHaveLength(8);
    await act(async () => button("收起项目分组").click());
    expect(container.querySelector('[data-project-id="p1"]')).toBeNull();
    expect(container.querySelectorAll(".task-list-row")).toHaveLength(7);
    await act(async () => button("收起任务分组").click());
    expect(container.querySelectorAll(".task-list-row")).toHaveLength(0);
    expect(button("展开任务分组").getAttribute("aria-expanded")).toBe("false");
    expect(button("新建项目")).not.toBeNull();
    await act(async () => root.unmount()); root = createRoot(container);
    await act(async () => render());
    expect(button("展开项目分组").getAttribute("aria-expanded")).toBe("false");
    expect(container.querySelectorAll(".task-list-row")).toHaveLength(0);
    await act(async () => button("展开任务分组").click());
    expect(container.querySelectorAll(".task-list-row")).toHaveLength(5);
    await act(async () => button("展开项目分组").click());
    expect(container.querySelectorAll(".task-list-row")).toHaveLength(6);
  } finally { await act(async () => root.unmount()); container.remove(); }
});


it("keeps a needs-input chip for read and background tasks, then clears it on reply", async () => {
  const pending = { ...task("succeeded", "input"), pending_input: true, last_read_at: "2026-09-07T00:00:00Z" };
  loadTasks.mockResolvedValue([pending, { ...task("succeeded", "background"), pending_input: true }]);
  const container = document.createElement("div"); document.body.appendChild(container);
  const root = createRoot(container);
  try {
    await act(async () => root.render(<TaskSidebar currentThreadId="input" collapsed={false} onToggle={() => {}} onSelect={() => {}} onNewTask={() => {}} />));
    expect(container.querySelectorAll(".task-input-badge")).toHaveLength(2);
    expect(container.querySelector('[aria-label="任务 input，需要输入"]')).not.toBeNull();
    expect(container.querySelectorAll(".status-unread")).toHaveLength(0);
    await act(async () => activityStore.publish(runActivitySchema.parse({
      run_id: "run-input", status: "running", started_at: "2026-09-07T00:01:00Z", items: [],
    })));
    expect(container.querySelector('[aria-label="任务 input，运行中"]')).not.toBeNull();
    expect(container.querySelectorAll(".task-input-badge")).toHaveLength(1);
    loadTasks.mockResolvedValue([{ ...pending, pending_input: false, run_id: "run-reply" }]);
    await act(async () => window.dispatchEvent(new Event("focus")));
    expect(container.querySelectorAll(".task-input-badge")).toHaveLength(0);
  } finally { await act(async () => root.unmount()); container.remove(); }
});

it("toggles the whole project heading and reserves navigation for View project", async () => {
  loadTasks.mockResolvedValue([{ ...task("succeeded", "project-task"), project_id: "project-fold" }]);
  const props = { currentThreadId: "", collapsed: false, onToggle() {}, onSelect: vi.fn(), onNewTask() {}, projects: [{ projectId: "project-fold", name: "折叠项目", userId: "test", tenantId: "local", createdAt: "", updatedAt: "" }] };
  const container = document.createElement("div"); document.body.appendChild(container);
  const root = createRoot(container);
  try {
    await act(async () => root.render(<TaskSidebar {...props} />));
    const heading = container.querySelector<HTMLButtonElement>('[aria-label="展开项目 折叠项目"]')!;
    expect(heading.tagName).toBe("BUTTON");
    expect(heading.textContent).toContain("折叠项目");
    expect(heading.querySelector("a")).toBeNull();
    await act(async () => heading.querySelector("strong")!.click());
    expect(container.querySelector('[aria-label="收起项目 折叠项目"]')).not.toBeNull();
    expect(container.querySelectorAll(".task-list-row")).toHaveLength(1);
    await act(async () => heading.click());
    expect(container.querySelectorAll(".task-list-row")).toHaveLength(0);
    await act(async () => container.querySelector<HTMLButtonElement>('[aria-label="项目操作 折叠项目"]')!.click());
    const view = container.querySelector<HTMLAnchorElement>('[role="menuitem"][href="/studio/projects/project-fold"]');
    expect(view?.textContent).toContain("查看项目");
    expect(props.onSelect).not.toHaveBeenCalled();
  } finally { await act(async () => root.unmount()); container.remove(); }
});


it("pins and unpins from the trailing control and shares the menu with project tasks", async () => {
  loadTasks.mockResolvedValue([task("succeeded", "first"), task("succeeded", "second")]);
  setTaskPinned.mockResolvedValue(undefined);
  const container = document.createElement("div"); document.body.appendChild(container);
  const root = createRoot(container);
  try {
    await act(async () => root.render(<TaskSidebar currentThreadId="first" collapsed={false}
      onToggle={() => {}} onSelect={() => {}} onNewTask={() => {}} />));
    const button = () => container.querySelector<HTMLButtonElement>('[aria-label="置顶 任务 second"], [aria-label="取消置顶 任务 second"]')!;
    await act(async () => button().click());
    expect(setTaskPinned).toHaveBeenCalledWith("second", true);
    expect(container.querySelectorAll(".task-list-row")[0].textContent).toContain("second");
    expect(button().getAttribute("aria-pressed")).toBe("true");
    await act(async () => button().click());
    expect(setTaskPinned).toHaveBeenLastCalledWith("second", false);
    expect(button().getAttribute("aria-pressed")).toBe("false");
    expect(container.querySelector(".task-sidebar-mode")?.closest(".task-list-scroll")).not.toBeNull();
    expect(container.querySelector(".task-sidebar-primary")?.closest(".task-list-scroll")).toBeNull();
  } finally { await act(async () => root.unmount()); container.remove(); }
});

it("restores five rows after collapsing a project, a section, or the whole sidebar", async () => {
  loadTasks.mockResolvedValue([
    ...Array.from({ length: 7 }, (_, i) => ({ ...task("succeeded", `project-${i}`), project_id: "project-a" })),
    ...Array.from({ length: 7 }, (_, i) => task("succeeded", `loose-${i}`)),
  ]);
  const props = { currentThreadId: "", collapsed: false, onToggle() {}, onSelect() {}, onNewTask() {},
    projects: [{ projectId: "project-a", name: "研究", userId: "test", tenantId: "local", createdAt: "", updatedAt: "" }] };
  const container = document.createElement("div"); document.body.append(container);
  const root = createRoot(container);
  const click = async (label: string) => {
    const button = container.querySelector<HTMLButtonElement>(`[aria-label="${label}"]`);
    expect(button, label).not.toBeNull();
    await act(async () => button!.click());
  };
  try {
    await act(async () => root.render(<TaskSidebar {...props} />));
    await click("展开项目 研究");
    expect(container.querySelectorAll(".task-list-row")).toHaveLength(10);
    await click("展开 研究 的任务");
    expect(container.querySelectorAll(".task-list-row")).toHaveLength(12);
    await click("收起项目 研究"); await click("展开项目 研究");
    expect(container.querySelectorAll(".task-list-row")).toHaveLength(10);
    const projectItems = container.querySelector('[data-project-id="project-a"] .task-project-items')!;
    expect(projectItems.lastElementChild?.textContent).toContain("展开显示（2）");
    await click("展开任务");
    expect(container.querySelectorAll(".task-list-row")).toHaveLength(12);
    await click("收起任务分组"); await click("展开任务分组");
    expect(container.querySelectorAll(".task-list-row")).toHaveLength(10);
    await click("展开 研究 的任务"); await click("展开任务");
    expect(container.querySelectorAll(".task-list-row")).toHaveLength(14);
    await act(async () => root.render(<TaskSidebar {...props} collapsed />));
    await act(async () => root.render(<TaskSidebar {...props} />));
    expect(container.querySelectorAll(".task-list-row")).toHaveLength(10);
  } finally { await act(async () => root.unmount()); container.remove(); }
});
