// @vitest-environment jsdom
// 云电脑跟随真实桌面占用自动展开：同一执行只弹一次，手动关闭后不弹回，新执行再联动。
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import Home from "../src/app/page";
import { PersonalAssistantEntry } from "../src/components/personal-assistant-context";
import { createUserScopedStorage, selectThread } from "../src/lib/thread-store";
import { invalidateClientReads } from "../src/lib/client-read-cache";

type Occupancy = { holder: { execution_id: string; objective_id?: string; objective_title?: string } | null };
const state = vi.hoisted(() => {
  const agent = { name: "agent", version: "1", ownerUserId: "user", scope: "personal", domain: "test" };
  return {
    agent, router: { push: vi.fn(), replace: vi.fn() }, projects: [],
    occupancy: null as Occupancy | null,
  };
});

vi.mock("next/navigation", () => ({ useRouter: () => state.router, useSearchParams: () => new URLSearchParams(window.location.search) }));
vi.mock("../src/components/auth-provider", () => ({ AuthProvider: ({ children }: { children: ReactNode }) => children, useAuth: () => ({ user: { user_id: "user" } }) }));
vi.mock("../src/lib/project-space", () => ({ loadTaskSummary: async () => null, projectHref: (id: string) => `/studio/projects/${id}` }));
vi.mock("../src/lib/task-agent-catalog", async importOriginal => ({ ...await importOriginal<object>(), loadTaskAgentCatalog: async () => ({ agents: [state.agent], defaultAgent: state.agent }) }));
vi.mock("../src/lib/task-model-catalog", async importOriginal => ({ ...await importOriginal<object>(), loadTaskModelRoutes: async () => [] }));
vi.mock("../src/lib/sidebar-state", () => ({ useSidebarProjects: () => [state.projects, vi.fn()] }));
vi.mock("../src/lib/activity-store", () => ({ useRunActivity: () => null, useRunViewModel: () => null }));
vi.mock("../src/lib/run-stream-store", () => ({ useRunStream: () => ({ status: "idle" }) }));
vi.mock("../src/lib/assistant-client", () => ({ assistantRequest: async (path: string) => {
  path = path.split("?")[0];
  if (path === "primary") return { specialistId: "primary", name: "主助理", description: "", slug: "primary", agentName: "agent", agentVersion: "1", status: "active", revision: 1, config: {}, createdAt: "", updatedAt: "" };
  return { specialist: { specialistId: path.split("/")[1], name: "子助理", agentName: "agent", agentVersion: "1", config: {} }, agentName: "agent", agentVersion: "1", agentOwnerUserId: "user", spaceId: null, threadId: "bot-original", primaryObjectiveId: null };
} }));
vi.mock("../src/components/task-sidebar", () => ({ TaskSidebar: () => null }));
vi.mock("../src/components/agent-thread", () => ({ AgentThread: () => <p data-thread /> }));
vi.mock("../src/components/assistant-runtime-shell", () => ({ AssistantRuntimeShell: ({ children }: { children: ReactNode }) => <div>{children}</div> }));
vi.mock("../src/components/assistant-channel-status", () => ({ AssistantChannelStatus: () => null }));
vi.mock("../src/components/assistant-progress-strip", () => ({ AssistantProgressStrip: () => null }));
vi.mock("../src/components/desktop-occupancy-chip", () => ({
  DesktopOccupancyChip: () => null,
  useDesktopOccupancy: () => state.occupancy,
  occupancyHolding: (data: Occupancy | null) => data?.holder?.execution_id ?? null,
}));
vi.mock("../src/components/assistant-computer-pane", () => ({
  AssistantComputerPane: ({ open, onClose, children }: { open: boolean; onClose: () => void; children: ReactNode }) =>
    <div data-computer-open={open}><button type="button" onClick={onClose}>关闭电脑</button>{children}</div>,
}));
vi.mock("../src/components/assistant-computer-link", () => ({ AssistantComputerLink: () => null }));
vi.mock("../src/components/workbench-rail", () => ({ WorkbenchRail: () => null }));
vi.mock("../src/components/productivity-command-center", () => ({ ProductivityCommandCenter: () => null }));
vi.mock("../src/components/specialist-setup-card", () => ({ SpecialistSetupCard: () => null }));
vi.mock("../src/components/task-header-actions", () => ({ TaskDetailsPopover: () => null, TaskHeaderActions: () => null }));
vi.mock("../src/components/bot-side-panel", () => ({ BotSidePanel: () => null }));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let host: HTMLDivElement;
let root: Root;
const storage = () => createUserScopedStorage(window.localStorage, "user");
const pane = () => host.querySelector("[data-computer-open]");
const openState = () => pane()?.getAttribute("data-computer-open");

beforeEach(() => {
  invalidateClientReads();
  const values = new Map<string, string>();
  vi.stubGlobal("localStorage", { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key) });
  vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
  window.history.replaceState(null, "", "/assistant");
  state.occupancy = null;
  selectThread(storage(), "old-thread");
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.unstubAllGlobals(); });

async function mount() {
  await act(async () => root.render(<PersonalAssistantEntry><Home /></PersonalAssistantEntry>));
}

async function setOccupancy(value: Occupancy | null) {
  state.occupancy = value;
  await mount();
}

it("opens the desktop for a real execution, once, and respects a manual close", async () => {
  await setOccupancy({ holder: { execution_id: "exec_1" } });
  expect(openState()).toBe("true");

  await act(async () => host.querySelector<HTMLButtonElement>("[data-computer-open] > button")!.click());
  expect(openState()).toBe("false");

  // Same execution still holding the desk must not pop the pane back open.
  await setOccupancy({ holder: { execution_id: "exec_1" } });
  expect(openState()).toBe("false");

  // The next execution is a new reason to show the desktop.
  await setOccupancy({ holder: { execution_id: "exec_2" } });
  expect(openState()).toBe("true");
});

it("stays closed when nobody holds the desk", async () => {
  await setOccupancy(null);
  expect(openState()).toBe("false");
});
