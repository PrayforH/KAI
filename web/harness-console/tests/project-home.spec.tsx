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
  results: vi.fn(),
  saveResult: vi.fn(),
  finalizeResult: vi.fn(),
  attachDocument: vi.fn(),
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
    results: mock.results,
    saveResult: mock.saveResult,
    finalizeResult: mock.finalizeResult,
    attachDocument: mock.attachDocument,
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
  ProjectModal: ({ title, children, onClose }: any) => (
    <div role="dialog" aria-label={title}>
      <h2>{title}</h2>
      <button onClick={onClose}>关闭面板</button>
      {children}
    </div>
  ),
  ProjectFilesDialog: () => <div>资料管理</div>,
  ProjectKnowledgeDialog: () => <div>知识管理</div>,
  ProjectSkillsDialog: () => <div>技能引用</div>,
}));
import { ProjectWorkspacePanel } from "../src/components/project-workspace-panel";
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
    removeItem: (key: string) => storage.delete(key),
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
  mock.results.mockResolvedValue([]);
  mock.saveResult.mockResolvedValue({
    resultId: "saved-1",
    projectId: "project-1",
    artifactId: "output-1",
    name: "分析报告.pdf",
    inputArtifactId: "snapshot-1",
    threadId: "old-archived-task",
    threadTitle: "历史分析任务",
    version: 1,
    sizeBytes: 2048,
    createdAt: "2026-10-17",
    finalizedAt: null,
  });
  mock.attachDocument.mockResolvedValue({});
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

it("manages configuration in the rail without creating tasks or adding a second composer", async () => {
  await act(async () => root.render(<ProjectWorkspacePanel projectId="project-1" />));
  expect(host.textContent).toContain("季度分析");
  expect(host.querySelector("textarea")).toBeNull();
  expect(host.textContent).toContain("连接器");
  expect(mock.createTask).not.toHaveBeenCalled();
  await click("文件和资源");
  expect(host.textContent).toContain("资料管理");
  expect(mock.createTask).not.toHaveBeenCalled();
});

it("keeps archived task outputs separate from inputs until the user collects one", async () => {
  window.history.replaceState(
    null,
    "",
    "/studio/projects/project-1?tab=results",
  );
  await act(async () =>
    root.render(<ProjectWorkspacePanel projectId="project-1" initialTab="results" />),
  );
  await click("从任务产物中保存");
  expect(host.textContent).toContain("历史分析任务 · 任务已归档");
  expect(
    host.querySelector('a[href="/?thread=old-archived-task"]'),
  ).not.toBeNull();
  expect(mock.attachDocument).not.toHaveBeenCalled();
  await click("保存到项目成果");
  expect(mock.saveResult).toHaveBeenCalledWith("project-1", "output-1");
  expect(mock.attachDocument).not.toHaveBeenCalled();
  await click("加入项目资料");
  expect(mock.attachDocument).toHaveBeenCalledWith("project-1", "snapshot-1");
  expect(host.textContent).toContain("加入项目资料");
});

it("writes project facts through the project service, preserving Agent ownership", async () => {
  window.history.replaceState(
    null,
    "",
    "/studio/projects/project-1?tab=resources",
  );
  await act(async () =>
    root.render(<ProjectWorkspacePanel projectId="project-1" initialTab="resources" />),
  );
  const textarea = host.querySelector('[role="dialog"] textarea')!;
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
  await act(async () => root.render(<ProjectWorkspacePanel projectId="project-1" />));
  expect(host.querySelector('[role="alert"]')?.textContent).toBe(
    "没有项目访问权限",
  );
  expect(host.textContent).toContain("重试");
  expect(mock.createTask).not.toHaveBeenCalled();
});

it("switches between inline results and configuration while preserving the thread URL", async () => {
  window.history.replaceState(null, "", "/?thread=existing-task");
  await act(async () => root.render(<ProjectWorkspacePanel projectId="project-1" />));
  await act(async () => root.render(<ProjectWorkspacePanel projectId="project-1" view="results" />));
  expect(host.textContent).toContain("还没有保存的项目成果");
  expect(host.querySelector('[role="dialog"]')).toBeNull();
  expect(window.location.search).toBe("?thread=existing-task");
  await act(async () => root.render(<ProjectWorkspacePanel projectId="project-1" view="project" />));
  expect(host.textContent).toContain("连接器");
  expect(host.textContent).not.toContain("还没有保存的项目成果");
});

it("restores an archived project without changing the published Agent", async () => {
  mock.resources.mockResolvedValue({
    project: { ...project, archivedAt: "2026-10-18" },
    documents: [],
    links: [],
    memories: [],
  });
  mock.update.mockResolvedValue(project);
  await act(async () => root.render(<ProjectWorkspacePanel projectId="project-1" />));
  await click("项目设置");
  await click("恢复项目");
  expect(mock.update).toHaveBeenCalledWith("project-1", { archived: false });
  expect(host.textContent).toContain("项目已恢复");

});

it("saves project instructions without renaming the project or changing Agent configuration", async () => {
  mock.update.mockResolvedValue({
    ...project,
    instructions: "先给结论，再标注数据来源",
  });
  await act(async () => root.render(<ProjectWorkspacePanel projectId="project-1" />));
  await click("指令");
  const textarea = host.querySelector('textarea[aria-label="项目指令"]')!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype,
      "value",
    )!.set!.call(textarea, "先给结论，再标注数据来源");
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () =>
    textarea
      .closest("form")!
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })),
  );
  expect(mock.update).toHaveBeenCalledWith("project-1", {
    instructions: "先给结论，再标注数据来源",
  });
  expect(host.querySelector('[role="dialog"]')).toBeNull();
  expect(host.textContent).toContain("季度分析");
});
