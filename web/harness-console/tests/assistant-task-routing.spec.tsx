// @vitest-environment jsdom
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { useAssistantSurface, useAssistantBot } from "../src/components/assistant-bot-context";
import Home from "../src/app/page";
import { PersonalAssistantEntry, PERSONAL_ASSISTANT_THREAD } from "../src/components/personal-assistant-context";
import { createUserScopedStorage, loadOrCreateThread, selectThread } from "../src/lib/thread-store";
import { invalidateClientReads } from "../src/lib/client-read-cache";

const delayed = vi.hoisted(() => ({enabled: false, resolve: undefined as ((value: unknown) => void) | undefined}));
const state = vi.hoisted(() => {
  const agent = { name: "agent", version: "1", ownerUserId: "user", scope: "personal", domain: "test" };
  return { primary: null as Record<string, unknown> | null, router: { push: vi.fn(), replace: vi.fn() }, agent, projects: [],
    task: { project_id: null as string | null, thread_id: "old-thread", title: "原历史任务", run_id: "old-run", agent_name: "agent", agent_version: "1", agent_owner_user_id: "user", status: "succeeded" } };
});
vi.mock("next/navigation", () => ({ useRouter: () => state.router, useSearchParams: () => new URLSearchParams(window.location.search) }));
vi.mock("../src/components/auth-provider", () => ({ AuthProvider: ({ children }: { children: ReactNode }) => children, useAuth: () => ({ user: { user_id: "user" } }) }));
vi.mock("../src/lib/project-space", () => ({ loadTaskSummary: async (id: string) => id === "old-thread" ? state.task : null, projectHref: (id: string) => `/studio/projects/${id}` }));
vi.mock("../src/lib/task-agent-catalog", async importOriginal => ({ ...await importOriginal<object>(), loadTaskAgentCatalog: async () => ({ agents: [state.agent], defaultAgent: state.agent }) }));
vi.mock("../src/lib/task-model-catalog", async importOriginal => ({ ...await importOriginal<object>(), loadTaskModelRoutes: async () => [] }));
vi.mock("../src/lib/sidebar-state", () => ({ useSidebarProjects: () => [state.projects, vi.fn()] }));
vi.mock("../src/lib/activity-store", () => ({ useRunActivity: () => null, useRunViewModel: () => null }));
vi.mock("../src/lib/run-stream-store", () => ({ useRunStream: () => ({ status: "idle" }) }));
vi.mock("../src/lib/assistant-client", () => ({ assistantRequest: async (path: string) => {
  path = path.split("?")[0];
  if (path === "primary") return state.primary;
  if (path === "specialists/bot-b" && delayed.enabled) return new Promise(resolve => {delayed.resolve = resolve;});
  return {specialist: {specialistId: path.split("/")[1], name: "子助理A", agentName: "agent", agentVersion: "1", config: {}},
    agentName: "agent", agentVersion: "1", agentOwnerUserId: "user", spaceId: null,
    threadId: path.includes("bot-b") ? "bot-second" : "bot-original", primaryObjectiveId: null};
} }));
vi.mock("../src/components/task-sidebar", () => ({ TaskSidebar: ({ onSelect, onNewTask }: { onSelect: (task: unknown) => void; onNewTask: () => void }) => <nav><button onClick={() => onSelect(state.task)}>打开历史任务</button><button onClick={onNewTask}>新建任务</button></nav> }));
vi.mock("../src/components/agent-thread", () => ({ AgentThread: ({ threadId }: { threadId: string }) => <p data-thread={threadId} data-assistant-surface={useAssistantSurface()} data-specialist={useAssistantBot()?.specialistId ?? ""}>{threadId}</p> }));
vi.mock("../src/components/assistant-runtime-shell", () => ({ AssistantRuntimeShell: ({ children, agentName }: { children: ReactNode; agentName: string }) => <div data-runtime-agent={agentName}>{children}</div> }));
vi.mock("../src/components/assistant-computer-pane", () => ({ AssistantComputerPane: ({ children }: { children: ReactNode }) => children }));
vi.mock("../src/components/assistant-channel-status", () => ({ AssistantChannelStatus: () => null }));
vi.mock("../src/components/assistant-progress-strip", () => ({ AssistantProgressStrip: () => null }));
vi.mock("../src/components/desktop-occupancy-chip", () => ({ DesktopOccupancyChip: () => null }));
vi.mock("../src/components/assistant-computer-link", () => ({ AssistantComputerLink: () => null }));
vi.mock("../src/components/workbench-rail", () => ({ WorkbenchRail: () => null }));
vi.mock("../src/components/productivity-command-center", () => ({ ProductivityCommandCenter: () => null }));
vi.mock("../src/components/specialist-setup-card", () => ({ SpecialistSetupCard: () => null }));
vi.mock("../src/components/task-header-actions", () => ({ TaskDetailsPopover: () => null, TaskHeaderActions: () => null }));
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let host: HTMLDivElement;
let root: Root;
beforeEach(() => {
  invalidateClientReads();
  const values = new Map<string, string>();
  vi.stubGlobal("localStorage", { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key) });
  window.history.replaceState(null, "", "/assistant");
  vi.clearAllMocks();
  delayed.enabled = false; delayed.resolve = undefined;
  state.task.project_id = null; state.primary = null;
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  selectThread(createUserScopedStorage(window.localStorage, "user"), "old-thread");
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.unstubAllGlobals(); });
const mount = async (assistant: boolean) => {
  await act(async () => root.render(assistant ? <PersonalAssistantEntry><Home /></PersonalAssistantEntry> : <Home />));
};
it.each([
  ["/assistant", PERSONAL_ASSISTANT_THREAD],
  ["/assistant?bot=bot-a", "bot-original"],
])("navigates from %s to a historical task without relabeling its runtime", async (url, originalThread) => {
  window.history.replaceState(null, "", url);
  await mount(true);
  expect(host.querySelector("[data-thread]")?.getAttribute("data-thread")).toBe(originalThread);
  expect(loadOrCreateThread(createUserScopedStorage(window.localStorage, "user"))).toBe("old-thread");
  await act(async () => host.querySelector<HTMLButtonElement>("nav button")!.click());
  expect(state.router.push).toHaveBeenCalledWith("/?thread=old-thread");
  expect(window.location.pathname).toBe("/assistant");
  expect(host.querySelector("[data-thread]")?.getAttribute("data-thread")).toBe(originalThread);
  window.history.replaceState(null, "", "/?thread=old-thread");
  await mount(false);
  expect(host.querySelector("[data-thread]")?.getAttribute("data-thread")).toBe("old-thread");
  expect(host.querySelector('[data-surface="task"]')).not.toBeNull();
  expect(host.querySelector("[data-thread]")?.getAttribute("data-assistant-surface")).toBe("false");
  expect(host.querySelector("main")?.getAttribute("data-conversation-surface")).toBe("task");
  window.history.replaceState(null, "", "/assistant");
  await mount(true);
  expect(host.querySelector("[data-thread]")?.getAttribute("data-thread")).toBe(PERSONAL_ASSISTANT_THREAD);
  expect(host.querySelector("[data-thread]")?.getAttribute("data-assistant-surface")).toBe("true");
});
it("does not reuse an empty assistant thread when starting an ordinary task", async () => {
  await mount(true);
  await act(async () => host.querySelectorAll<HTMLButtonElement>("nav button")[1]!.click());
  const href = state.router.push.mock.calls[0]?.[0];
  expect(href).toMatch(/^\/\?thread=/);
  expect(new URL(href, "https://kai.test").searchParams.get("thread")).not.toBe(PERSONAL_ASSISTANT_THREAD);
  expect(host.querySelector("[data-thread]")?.getAttribute("data-thread")).toBe(PERSONAL_ASSISTANT_THREAD);
  expect(host.querySelector("[data-thread]")?.getAttribute("data-assistant-surface")).toBe("true");
});
it("moves a legacy assistant task URL to the original task page, retaining its focused run", async () => {
  window.history.replaceState(null, "", "/assistant?thread=old-thread&run=old-run");
  await mount(true);
  expect(state.router.replace).toHaveBeenCalledWith("/?thread=old-thread&run=old-run");
  expect(host.querySelector("[data-thread]")).toBeNull();
});

it("normalizes an old main-assistant link without leaving the same page empty", async () => {
  window.history.replaceState(null, "", "/assistant?thread=kai-personal-assistant-v1");
  await mount(true);
  expect(state.router.replace).toHaveBeenCalledWith("/assistant");
  expect(host.querySelector("[data-thread]")?.getAttribute("data-thread")).toBe(PERSONAL_ASSISTANT_THREAD);
  expect(host.querySelector("[data-thread]")?.getAttribute("data-assistant-surface")).toBe("true");
});


it("keeps project-linked tasks in the workbench surface, including browser navigation back from a Bot", async () => {
  state.task.project_id = "project-audit";
  window.history.replaceState(null, "", "/?thread=old-thread");
  await mount(false);
  expect(host.querySelector("main")?.getAttribute("data-project-task")).toBe("true");
  expect(host.querySelector("main")?.getAttribute("data-conversation-surface")).toBe("task");
  expect(host.querySelector("[data-thread]")?.getAttribute("data-assistant-surface")).toBe("false");
  window.history.replaceState(null, "", "/assistant?bot=bot-a");
  await mount(true);
  expect(host.querySelector("[data-thread]")?.getAttribute("data-assistant-surface")).toBe("true");
  window.history.replaceState(null, "", "/?thread=old-thread");
  await mount(false);
  expect(host.querySelector("main")?.getAttribute("data-project-task")).toBe("true");
  expect(host.querySelector("[data-thread]")?.getAttribute("data-assistant-surface")).toBe("false");
});

it("switches Bot A → Bot B → main in the mounted page without remounting its sidebar", async () => {
  window.history.replaceState(null, "", "/assistant?bot=bot-a");
  await mount(true);
  const nav = host.querySelector("nav");
  expect(host.querySelector('[data-thread="bot-original"]')).not.toBeNull();
  window.history.pushState(null, "", "/assistant?bot=bot-b");
  await mount(true);
  expect(host.querySelector("nav")).toBe(nav);
  expect(host.querySelector('[data-thread="bot-second"]')).not.toBeNull();
  expect(host.querySelector('[data-thread="bot-original"]')).toBeNull();
  window.history.pushState(null, "", "/assistant");
  await mount(true);
  expect(host.querySelector("nav")).toBe(nav);
  expect(host.querySelector(`[data-thread="${PERSONAL_ASSISTANT_THREAD}"]`)).not.toBeNull();
  expect(host.querySelector('[data-thread="bot-second"]')).toBeNull();
  expect(loadOrCreateThread(createUserScopedStorage(window.localStorage, "user"))).toBe("old-thread");
});


it("never mounts the previous Bot under a new identity, and ignores a late detail after returning to main", async () => {
  window.history.replaceState(null, "", "/assistant?bot=bot-a"); await mount(true);
  delayed.enabled = true;
  window.history.pushState(null, "", "/assistant?bot=bot-b"); await mount(true);
  expect(host.querySelector("[data-thread]")).toBeNull();
  window.history.pushState(null, "", "/assistant"); await mount(true);
  await act(async () => delayed.resolve?.({specialist: {specialistId: "bot-b", name: "晚到的Bot", agentName: "agent", agentVersion: "1", config: {}}, threadId: "bot-second", primaryObjectiveId: null}));
  expect(host.querySelector(`[data-thread="${PERSONAL_ASSISTANT_THREAD}"]`)).not.toBeNull();
  expect(host.textContent).not.toContain("晚到的Bot");
});


it("editing main contact identity never replaces its actual task Agent binding", async () => {
  state.primary = {specialistId: "specialist_primary_test", name: "自定义总管", description: "我定义的角色", agentName: "profile-coordinate", agentVersion: "9", status: "ready", config: {}};
  await mount(true);
  expect(host.textContent).toContain("自定义总管");
  expect(host.querySelector('[data-runtime-agent]')?.getAttribute("data-runtime-agent")).toBe("agent");
  expect(host.querySelector(`[data-thread="${PERSONAL_ASSISTANT_THREAD}"]`)).not.toBeNull();
  expect(host.querySelector('[data-thread]')?.getAttribute('data-specialist')).toBe('');
});
