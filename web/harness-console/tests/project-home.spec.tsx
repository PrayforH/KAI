// @vitest-environment jsdom
import { act, StrictMode } from "react";
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
  modelRoutes: vi.fn(),
  capabilities: vi.fn(),
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
  useRouter: () => ({ push: mock.push, prefetch: vi.fn() }),
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
    capabilities: mock.capabilities,
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
vi.mock("../src/lib/task-model-catalog", async original => ({
  ...(await original<object>()), loadTaskModelRoutes: mock.modelRoutes,
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
  mock.modelRoutes.mockResolvedValue([]);
  mock.createTask.mockResolvedValue({ thread_id: "new-project-task" });
  mock.capabilities.mockResolvedValue({ mcpServers: [
    {reference: "one", label: "连接器一", enabled: true, endpointUrl: "https://example.test/one", visibility: "all"},
    {reference: "two", label: "连接器二", enabled: true, endpointUrl: "https://example.test/two", visibility: "all"},
  ] });
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

it("opens an independent organizing home without creating a task or mounting a conversation", async () => {
  await act(async () => root.render(<ProjectHome projectId="project-1" />));
  expect(host.querySelector("h1")?.textContent).toBe("季度分析");
  expect(host.querySelector('[aria-label="刷新项目"]')).toBeNull();
  expect([...host.querySelectorAll("button")].some(button => button.textContent === "项目设置")).toBe(false);
  expect(host.querySelector('[role="tablist"]')).toBeNull();
  expect(host.textContent).toContain("文件和资源");
  expect(host.textContent).toContain("连接器");
  expect(host.textContent).toContain("项目成果");
  expect(host.querySelector(".assistant-runtime-shell")).toBeNull();
  expect(mock.createTask).not.toHaveBeenCalled();
  await click("上传文件或添加链接");
  expect(host.textContent).toContain("资料管理");
  expect(mock.createTask).not.toHaveBeenCalled();
});

it("passes the chosen published Agent coordinate and project into the shared task entry", async () => {
  await act(async () => root.render(<ProjectHome projectId="project-1" />));
  await click("选择分析助手");
  const textarea = host.querySelector('textarea[aria-label="项目任务需求"]')!;
  await act(async () => {
    Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype,
      "value",
    )!.set!.call(textarea, "分析本季度经营数据");
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () =>
    textarea
      .closest("form")!
      .dispatchEvent(new Event("submit", { bubbles: true, cancelable: true })),
  );
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
    root.render(<ProjectHome projectId="project-1" initialTab="resources" />),
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
    host.querySelector('[role="dialog"][aria-label="项目成果"]'),
  ).not.toBeNull();
  window.history.replaceState(null, "", "/studio/projects/project-1");
  await act(async () => root.render(<ProjectHome projectId="project-1" />));
  expect(host.querySelector('[role="dialog"]')).toBeNull();
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
  window.history.replaceState(null, "", "/studio/projects/project-1?settings=1");
  await act(async () => root.render(<ProjectHome projectId="project-1" />));
  await click("恢复项目");
  expect(mock.update).toHaveBeenCalledWith("project-1", { archived: false });
  expect(host.textContent).toContain("项目已恢复");
  expect(
    (
      host.querySelector(
        'textarea[aria-label="项目任务需求"]',
      ) as HTMLTextAreaElement
    ).disabled,
  ).toBe(false);
});

it("saves project instructions without renaming the project or changing Agent configuration", async () => {
  mock.update.mockResolvedValue({
    ...project,
    instructions: "先给结论，再标注数据来源",
  });
  await act(async () => root.render(<ProjectHome projectId="project-1" />));
  await click("编辑指令");
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
  expect(host.querySelector("h1")?.textContent).toBe("季度分析");
  expect(host.querySelector('[aria-label="刷新项目"]')).toBeNull();
  expect([...host.querySelectorAll("button")].some(button => button.textContent === "项目设置")).toBe(false);
});


it("opens platform skills from the add dropdown without navigating or creating a task", async () => {
  await act(async () => root.render(<ProjectHome projectId="project-1" />));
  const trigger = host.querySelector<HTMLButtonElement>('[aria-label="添加项目技能"]')!;
  await act(async () => trigger.click());
  const menu = document.querySelector('[role="menu"][aria-label="添加项目技能"]')!;
  expect(menu.textContent).toContain("从平台技能添加");
  expect(menu.textContent).not.toContain("GitHub");
  await act(async () => menu.querySelector<HTMLButtonElement>("button")!.click());
  expect(host.textContent).toContain("技能引用");
  expect(document.querySelector('[role="menu"][aria-label="添加项目技能"]')).toBeNull();
  expect(mock.createTask).not.toHaveBeenCalled();
  expect(mock.push).not.toHaveBeenCalled();
});

it("saves all checked MCP bindings across consecutive checkbox changes", async () => {
  await act(async () => root.render(<StrictMode><ProjectHome projectId="project-1" /></StrictMode>));
  await click("＋ 添加");
  const boxes = [...host.querySelectorAll<HTMLInputElement>('input[type="checkbox"]')];
  expect(boxes).toHaveLength(2);
  await act(async () => boxes[0].click());
  expect(boxes[0].checked).toBe(true);
  await act(async () => boxes[1].click());
  expect(boxes[1].checked).toBe(true);
  mock.resources.mockResolvedValue({project: {...project, mcpReferences: ["one", "two"]}, documents: [], links: [], memories: []});
  await click("保存关联");
  expect(mock.update).toHaveBeenCalledWith("project-1", {mcpReferences: ["one", "two"]});
  expect(host.querySelector('[role="dialog"]')).toBeNull();
  expect(host.textContent).toContain("已关联 2 个连接器");
  expect(host.textContent).toContain("已保存 2 个项目连接器");
});

it("keeps connector selection open if the read-back does not confirm the binding", async () => {
  await act(async () => root.render(<ProjectHome projectId="project-1" />));
  await click("＋ 添加");
  await act(async () => host.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click());
  await click("保存关联");
  expect(host.querySelector('[role="dialog"]')).not.toBeNull();
  expect(host.textContent).toContain("连接器关联未完整保存");
  expect(host.querySelector<HTMLInputElement>('input[type="checkbox"]')!.checked).toBe(true);
});

it("hides each card's onboarding text only when that card has content", async () => {
  mock.resources.mockResolvedValue({
    project: { ...project, skillReferences: ["research"], knowledgeReferences: ["kb-1"] },
    documents: [{ inputArtifactId: "file-1", name: "说明.pdf" }], links: [], memories: [],
  });
  await act(async () => root.render(<ProjectHome projectId="project-1" />));
  expect(host.textContent).toContain("说明.pdf");
  expect(host.textContent).toContain(project.instructions);
  expect(host.textContent).not.toContain("指导助手如何在此项目的所有任务中响应。");
  expect(host.textContent).not.toContain("添加文件和网页，供本项目的所有任务使用。");
  expect(host.textContent).not.toContain("为项目任务补充可复用的工作流程。");
  expect(host.textContent).not.toContain("关联已有知识库，供任务检索。");
  expect(host.textContent).toContain("从任务中保存交付物，在这里集中查看。");
});

it("keeps project launch out of IME confirmation and hands the draft to the main conversation", async () => {
  await act(async () => root.render(<ProjectHome projectId="project-1" />));
  const textarea = host.querySelector<HTMLTextAreaElement>('[aria-label="项目任务需求"]')!;
  expect(textarea.closest(".harness-composer-shell")).not.toBeNull();
  expect(host.querySelector('[data-conversation-action="send"]')).not.toBeNull();
  await act(async () => {
    textarea.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true }));
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(textarea, "项目需求");
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
    textarea.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    textarea.dispatchEvent(new CompositionEvent("compositionend", { bubbles: true }));
    textarea.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  });
  expect(mock.createTask).not.toHaveBeenCalled();
  await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="发送并开始项目任务"]')!.click());
  expect(mock.createTask).toHaveBeenCalledTimes(1);
  expect(mock.push).toHaveBeenCalledWith("/?thread=new-project-task");
});


it("carries the chosen model into the user-scoped main-conversation task settings", async () => {
  mock.modelRoutes.mockResolvedValue([{ id: "claude-test", label: "Claude", modelType: "chat", capabilities: ["chat"] }]);
  await act(async () => root.render(<ProjectHome projectId="project-1" />));
  const select = host.querySelector<HTMLSelectElement>('[aria-label="选择本次任务使用的模型"]')!;
  const textarea = host.querySelector<HTMLTextAreaElement>('[aria-label="项目任务需求"]')!;
  await act(async () => {
    select.value = "claude-test";
    select.dispatchEvent(new Event("change", { bubbles: true }));
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(textarea, "分析项目");
    textarea.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => host.querySelector<HTMLButtonElement>('[aria-label="发送并开始项目任务"]')!.click());
  const { createUserScopedStorage } = await import("../src/lib/thread-store");
  const { loadTaskModelOverride } = await import("../src/lib/task-model-catalog");
  expect(loadTaskModelOverride(createUserScopedStorage(localStorage, "owner"), "new-project-task")).toBe("claude-test");
  expect(loadTaskModelOverride(createUserScopedStorage(localStorage, "another-user"), "new-project-task")).toBeNull();
});
