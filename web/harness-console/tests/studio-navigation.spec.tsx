// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { StudioUnifiedShell } from "../src/components/agent-studio/studio-unified-shell";
import { createUserScopedStorage, loadOrCreateThread, selectThread } from "../src/lib/thread-store";
const { push, createProjectTask } = vi.hoisted(() => ({ push: vi.fn(), createProjectTask: vi.fn() }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push }), usePathname: () => "/studio/capabilities" }));
vi.mock("../src/components/auth-provider", () => ({ useAuth: () => ({ user: { user_id: "test" } }) }));
vi.mock("../src/lib/interface-preferences", () => ({ useInternalAgentsPreference: () => [false] }));
vi.mock("../src/lib/sidebar-state", () => ({ useSidebarProjects: () => [[], () => {}] }));
vi.mock("../src/lib/task-agent-catalog", () => ({ loadTaskAgentCatalog: async () => ({ agents: [], defaultAgent: null }) }));
vi.mock("../src/lib/project-task", () => ({ createProjectTask }));
vi.mock("../src/components/productivity-command-center", () => ({ ProductivityCommandCenter: () => null }));
vi.mock("../src/components/task-sidebar", () => ({ TaskSidebar: (props: { onNewTask: () => void; onNewTaskWithProject: (project: { projectId: string }) => Promise<void> }) => <>
  <button id="new" onClick={props.onNewTask}>New</button>
  <button id="project" onClick={() => void props.onNewTaskWithProject({ projectId: "p1" })}>Project</button>
</> }));
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
beforeEach(() => {
  push.mockReset(); createProjectTask.mockReset();
  const values = new Map<string, string>();
  vi.stubGlobal("localStorage", { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key) });
});
afterEach(() => vi.unstubAllGlobals());
it("navigates New Task to a fresh ID instead of restoring the last conversation", async () => {
  const storage = createUserScopedStorage(localStorage, "test"); selectThread(storage, "old-task");
  const host = document.createElement("div"); const root = createRoot(host);
  try {
    await act(async () => root.render(<StudioUnifiedShell>Plugins</StudioUnifiedShell>));
    await act(async () => host.querySelector<HTMLButtonElement>("#new")!.click());
    const href = push.mock.calls[0][0] as string;
    const id = new URL(href, "http://test").searchParams.get("thread");
    expect(id).toBeTruthy(); expect(id).not.toBe("old-task"); expect(loadOrCreateThread(storage)).toBe(id);
  } finally { await act(async () => root.unmount()); }
});
it("does not let a pending project creation replace a newer New Task click", async () => {
  let resolve!: (task: { thread_id: string }) => void;
  createProjectTask.mockReturnValue(new Promise((done) => { resolve = done; }));
  const host = document.createElement("div"); const root = createRoot(host);
  try {
    await act(async () => root.render(<StudioUnifiedShell>Plugins</StudioUnifiedShell>));
    await act(async () => host.querySelector<HTMLButtonElement>("#project")!.click());
    await act(async () => host.querySelector<HTMLButtonElement>("#new")!.click());
    await act(async () => resolve({ thread_id: "late-project-task" }));
    expect(push).toHaveBeenCalledTimes(1); expect(push.mock.calls[0][0]).not.toContain("late-project-task");
  } finally { await act(async () => root.unmount()); }
});
