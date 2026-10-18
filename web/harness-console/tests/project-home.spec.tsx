// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
const mock = vi.hoisted(() => ({
  push: vi.fn(),
  resources: vi.fn(),
  update: vi.fn(),
  addMemory: vi.fn(),
  updateMemory: vi.fn(),
  removeMemory: vi.fn(),
  archive: vi.fn(),
  createTask: vi.fn(),
  collect: vi.fn(),
  catalog: vi.fn(),
  agents: [
    {
      name: "lead",
      version: "1",
      displayName: "通用助手",
      domain: "general",
      ownerUserId: "owner",
      canChat: true,
    },
    {
      name: "analyst",
      version: "2",
      displayName: "分析助手",
      domain: "analysis",
      ownerUserId: "colleague",
      spaceId: "team-1",
      scope: "team",
      canChat: true,
    },
  ],
}));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mock.push }),
  useSearchParams: () => new URLSearchParams(window.location.search),
}));
vi.mock("../src/components/auth-provider", () => ({
  useAuth: () => ({ user: { user_id: "owner" } }),
}));
vi.mock("../src/lib/studio-client", () => ({
  projectClient: {
    resources: mock.resources,
    update: mock.update,
    addMemory: mock.addMemory,
    updateMemory: mock.updateMemory,
    removeMemory: mock.removeMemory,
    archive: mock.archive,
  },
  studioClient: {
    listPlatformSkills: async () => ({ packages: [] }),
    listKnowledgeBases: async () => [],
  },
}));
vi.mock("../src/lib/task-agent-catalog", () => ({
  loadTaskAgentCatalog: mock.catalog,
  chatUsableAgents: (agents: any[]) => agents.filter((agent) => agent.canChat),
  agentItemKey: (agent: any) =>
    `${agent.ownerUserId}:${agent.spaceId ?? ""}:${agent.name}@${agent.version}`,
}));
vi.mock("../src/lib/project-task", () => ({
  createProjectTask: mock.createTask,
}));
vi.mock("../src/lib/task-history", () => ({ notifyTaskListChanged: vi.fn() }));
vi.mock("../src/lib/project-space", async (original) => ({
  ...(await original<object>()),
  addResultToProject: mock.collect,
}));
vi.mock("../src/components/confirmation-dialog", () => ({
  useConfirmationDialog: () => ({
    requestConfirmation: async () => true,
    confirmationDialog: null,
  }),
}));
vi.mock("../src/components/task-agent-switcher", () => ({
  TaskAgentSwitcher: ({ selected, onChange }: any) => (
    <button onClick={() => onChange(mock.agents[1])}>
      {selected?.displayName} · 选择分析助手
    </button>
  ),
}));
vi.mock("../src/components/project-resource-dialogs", () => ({
  ProjectFilesDialog: () => <div>资料管理</div>,
  ProjectKnowledgeDialog: () => <div>知识管理</div>,
  ProjectSkillsDialog: () => <div>技能引用</div>,
}));
import { ProjectHome } from "../src/components/project-home";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let host: HTMLDivElement;
let root: ReturnType<typeof createRoot>;
const project = {
  tenantId: "tenant",
  userId: "owner",
  projectId: "project-1",
  name: "季度分析",
  instructions: "围绕本季度的交付目标",
  createdAt: "2026-10-18",
  updatedAt: "2026-10-18",
};
const artifact = {
  artifact_id: "output-1",
  name: "分析报告.pdf",
  media_type: "application/pdf",
  size_bytes: 2048,
  thread_id: "old-archived-task",
  thread_title: "历史分析任务",
  task_archived: true,
  created_at: "2026-10-17",
};
const click = async (label: string) => {
  const button = [...host.querySelectorAll("button")].find((item) =>
    item.textContent?.includes(label),
  );
  expect(button, label).toBeTruthy();
  await act(async () => button!.click());
};
beforeEach(() => {
  vi.clearAllMocks();
  const storage = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
  });
  window.history.replaceState(null, "", "/studio/projects/project-1");
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  mock.resources.mockResolvedValue({
    project,
    documents: [],
    links: [],
    memories: [],
  });
  mock.catalog.mockResolvedValue({
    agents: mock.agents,
    defaultAgent: mock.agents[0],
  });
  mock.createTask.mockResolvedValue({ thread_id: "new-project-task" });
  mock.collect.mockResolvedValue(undefined);
  mock.addMemory.mockResolvedValue({});
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string) =>
      Response.json(String(input).startsWith("/api/agui") ? [] : [artifact]),
    ),
  );
});
afterEach(() => {
  act(() => root?.unmount());
  host?.remove();
  vi.unstubAllGlobals();
});

it("opens an independent organizing home without creating a task or mounting a conversation", async () => {
  await act(async () => root.render(<ProjectHome projectId="project-1" />));
  expect(host.querySelector("h1")?.textContent).toBe("季度分析");
  expect(
    [...host.querySelectorAll('[role="tab"]')].map((node) => node.textContent),
  ).toEqual(["概览", "任务", "资料", "成果"]);
  expect(host.querySelector(".assistant-runtime-shell")).toBeNull();
  expect(mock.createTask).not.toHaveBeenCalled();
  await click("资料");
  expect(window.location.search).toBe("?tab=resources");
  expect(host.textContent).toContain("当前项目");
});

it("passes the chosen published Agent coordinate and project into the shared task entry", async () => {
  await act(async () => root.render(<ProjectHome projectId="project-1" />));
  await click("选择分析助手");
  await click("新建任务 →");
  expect(mock.createTask).toHaveBeenCalledWith("project-1", mock.agents[1]);
  expect(mock.push).toHaveBeenCalledWith("/?thread=new-project-task");
});

it("keeps archived task outputs separate from inputs until the user collects one", async () => {
  window.history.replaceState(
    null,
    "",
    "/studio/projects/project-1?tab=results",
  );
  await act(async () =>
    root.render(<ProjectHome projectId="project-1" initialTab="results" />),
  );
  expect(host.textContent).toContain("历史分析任务 · 任务已归档");
  expect(
    host.querySelector('a[href="/?thread=old-archived-task"]'),
  ).not.toBeNull();
  expect(mock.collect).not.toHaveBeenCalled();
  await click("加入项目资料");
  expect(mock.collect).toHaveBeenCalledWith("project-1", artifact);
  expect(host.textContent).toContain("已加入资料");
});

it("writes project facts through the project service, preserving Agent ownership", async () => {
  window.history.replaceState(
    null,
    "",
    "/studio/projects/project-1?tab=resources",
  );
  await act(async () =>
    root.render(<ProjectHome projectId="project-1" initialTab="resources" />),
  );
  const textarea = host.querySelector("textarea")!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype,
      "value",
    )!.set!.call(textarea, "本季度采用新的统计口径");
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () =>
    textarea
      .closest("form")!
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })),
  );
  expect(mock.addMemory).toHaveBeenCalledWith(
    "project-1",
    "本季度采用新的统计口径",
  );
  expect(host.querySelector('a[href="/studio/agents"]')).toBeNull();
});

it("does not represent a failed project read as an empty valid project", async () => {
  mock.resources.mockRejectedValue(new Error("没有项目访问权限"));
  await act(async () => root.render(<ProjectHome projectId="project-1" />));
  expect(host.querySelector('[role="alert"]')?.textContent).toBe(
    "没有项目访问权限",
  );
  expect(host.textContent).toContain("重试");
  expect(mock.createTask).not.toHaveBeenCalled();
});

it("follows tab navigation within the same project route", async () => {
  await act(async () => root.render(<ProjectHome projectId="project-1" />));
  window.history.replaceState(
    null,
    "",
    "/studio/projects/project-1?tab=results",
  );
  await act(async () =>
    root.render(<ProjectHome projectId="project-1" initialTab="results" />),
  );
  expect(
    host.querySelector('[role="tab"][aria-selected="true"]')?.textContent,
  ).toBe("成果");
  window.history.replaceState(null, "", "/studio/projects/project-1");
  await act(async () => root.render(<ProjectHome projectId="project-1" />));
  expect(
    host.querySelector('[role="tab"][aria-selected="true"]')?.textContent,
  ).toBe("概览");
});

it("restores an archived project without changing the published Agent", async () => {
  mock.resources.mockResolvedValue({
    project: { ...project, archivedAt: "2026-10-18" },
    documents: [],
    links: [],
    memories: [],
  });
  mock.update.mockResolvedValue(project);
  await act(async () => root.render(<ProjectHome projectId="project-1" />));
  await click("项目设置");
  await click("恢复项目");
  expect(mock.update).toHaveBeenCalledWith("project-1", { archived: false });
  expect(host.textContent).toContain("项目已恢复");
  expect(
    [...host.querySelectorAll("button")].find(
      (button) => button.textContent === "新建任务 →",
    )?.disabled,
  ).toBe(false);
});
