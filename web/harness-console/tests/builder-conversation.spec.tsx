// @vitest-environment jsdom
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AgentBuilderAssistant } from "../src/components/agent-studio/agent-builder-overlays";
import { DEFAULT_STUDIO_DRAFT, type StudioDraft } from "../src/lib/agent-studio";
import { studioClient, studioDraftToSpec, type ApiAgentDraft, type StudioTryRun } from "../src/lib/studio-client";

vi.mock("../src/components/auth-provider", () => ({useAuth: () => ({user:{user_id:"preview-user"},membership:{role:"owner"}})}));
vi.mock("../src/components/agent-studio/agent-project-code", () => ({ AgentProjectCode: ({comparison, comparisonPending}: {comparison?: {before:{revision:number};after:{revision:number}};comparisonPending:boolean}) => <section aria-label="测试代码差异">{comparison?.before.revision} → {comparison?.after.revision} · {comparisonPending ? "待应用" : "已应用"}</section> }));
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root;
let host: HTMLDivElement;
let toggleOpen: (value: boolean) => void;
let setDirty: (value: boolean) => void;
let newDraft: () => void;
let enableWorkspace: () => void;
let switchMode: (mode: "build" | "chat") => void;
const initial: StudioDraft = { ...DEFAULT_STUDIO_DRAFT, id: "draft-multi", revision: 1 };
const updated = vi.fn();
function api(draft: StudioDraft): ApiAgentDraft {
  return { draftId: draft.id, revision: draft.revision, spec: studioDraftToSpec(draft),
    agentId: null, spaceId: null, tenantId: "a", createdBy: "a", updatedBy: "a",
    createdAt: "2026-09-07", updatedAt: "2026-09-07", publishedVersion: null,
    publishedHash: null, publishedPackageHash: null };
}
const run = { draftRevision: 1, run: { run_id: "run-1", status: "succeeded" },
  events: [], loop: [], approvals: [], artifacts: [], finalText: "试跑结果" } as unknown as StudioTryRun;
beforeEach(() => {
  // Transfers prefer XHR so they can report progress; these tests stub fetch,
  // so they pin the composer to the fetch path.
  vi.stubGlobal("XMLHttpRequest", undefined);
  const storage = new Map<string, string>();
  vi.stubGlobal("localStorage", {getItem:(key: string) => storage.get(key) ?? null, setItem:(key: string, value: string) => storage.set(key, value), removeItem:(key: string) => storage.delete(key), clear:() => storage.clear()});
  HTMLElement.prototype.scrollTo = vi.fn();
  HTMLElement.prototype.scrollIntoView = vi.fn();
  vi.stubGlobal("ResizeObserver", class {observe(){} unobserve(){} disconnect(){}});
  vi.stubGlobal("IntersectionObserver", class {observe(){} unobserve(){} disconnect(){}});
  vi.spyOn(studioClient, "listKnowledgeBases").mockResolvedValue([]);
  vi.spyOn(studioClient, "listTryRuns").mockResolvedValue([]);
  vi.spyOn(studioClient, "readBuilderMaterials").mockResolvedValue({context: "参考材料正文"});
  let nextRun = 0;
  const sessionByRun = new Map<string, string>();
  vi.spyOn(studioClient, "createTryRun").mockImplementation(async (_id, revision, _prompt, _key, options) => {
    const runId = `run-${++nextRun}`;
    const sessionId = (options?.continueFromRunId && sessionByRun.get(options.continueFromRunId)) || `session-${runId}`;
    sessionByRun.set(runId, sessionId);
    return { ...run, draftRevision: revision, run: { ...run.run, run_id: runId, session_id: sessionId } };
  });
  vi.spyOn(studioClient, "streamTryRunEvents").mockResolvedValue();
  vi.spyOn(studioClient, "getTryRun").mockImplementation(async (_id, revision, runId) => ({ ...run, draftRevision: revision, run: { ...run.run, run_id: runId, session_id: "preview-session" } }));
  vi.spyOn(studioClient, "converseBuilder").mockResolvedValue({
    baseRevision: 1, reply: "建议输出表格", changedFields: ["systemPrompt"], changes: { systemPrompt: "输出表格" },
  });
  vi.spyOn(studioClient, "previewBuilderProjectDiff").mockRejectedValue(new Error("Export unavailable in this test"));
  vi.spyOn(studioClient, "applyBuilderEdit").mockResolvedValue(api({ ...initial, revision: 2, systemPrompt: "输出表格" }));
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  function Harness() {
    const [draft, setDraft] = useState(initial);
    const [playgroundMode, setPlaygroundMode] = useState<"build" | "chat">("build");
    switchMode = setPlaygroundMode;
    const [embedded, setEmbedded] = useState(false);
    const [target, setTarget] = useState<HTMLDivElement | null>(null);
    enableWorkspace = () => setEmbedded(true);
    const [open, openChange] = useState(true);
    const [dirty, dirtyChange] = useState(false);
    const [mode, setMode] = useState<"create" | "run">("run");
    const [creationSession, setCreationSession] = useState(0);
    toggleOpen = openChange; setDirty = dirtyChange;
    newDraft = () => { setMode("create"); setCreationSession((value) => value + 1); };
    return <><div ref={setTarget} /><AgentBuilderAssistant playgroundMode={playgroundMode} onCollapseConfiguration={() => setPlaygroundMode("chat")} onExpandConfiguration={() => setPlaygroundMode("build")} workspaceTarget={embedded ? target : undefined} open={open} mode={mode} creationSession={creationSession} draft={draft} initialPrompt=""
      recommendation={null} knowledgeMcpReferences={[]} hasUnsavedChanges={dirty}
      onClose={() => openChange(false)} onCreated={(flow) => { setDraft(flow.draft); setMode("run"); }} prepareDraft={async () => draft}
      onUpdated={(next) => { updated(next); setDraft(next); }} /></>;
  }
  act(() => root.render(<Harness />));
});
afterEach(() => { vi.unstubAllGlobals(); act(() => root.unmount()); host.remove(); vi.restoreAllMocks(); updated.mockReset(); });
async function click(text: string) {
  await act(async () => { [...host.querySelectorAll<HTMLButtonElement | HTMLInputElement>("button, input[type=checkbox]")].find((button) => (button.textContent === text || button.getAttribute("aria-label") === text))!.click(); });
}
// Starting a conversation and switching between them share one menu, so the
// new-conversation row lives behind the session summary.
async function startNewConversation() {
  await act(async () => {
    (host.querySelector('[aria-label="会话：新建或切换"]') as HTMLElement).click();
  });
  await act(async () => {
    const menu = host.querySelector('[aria-label="会话：新建或切换"]')!.closest("details")!;
    [...menu.querySelectorAll("button")].find((button) => button.textContent?.includes("新对话"))!.click();
  });
}
async function send(value: string) {
  await act(async () => {
    const input = host.querySelector<HTMLTextAreaElement>('[aria-label="智能体构建助手"] textarea:not([aria-label="对话预览输入"])') ?? host.querySelector<HTMLTextAreaElement>('[aria-label="消息输入"]')!;
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => { const button = host.querySelector('[aria-label="发送消息"]') as HTMLButtonElement | null; if (button) button.click(); else host.querySelector('[aria-label="智能体构建助手"] textarea')!.dispatchEvent(new KeyboardEvent("keydown", {key: "Enter", bubbles: true})); });
}

it("reviews multiple edits on the same draft and reruns the original test, never the edit instruction", async () => {
  await click("试跑");
  await send("测试原始材料");
  await click("修改配置");
  await send("输出改成表格");
  expect(studioClient.applyBuilderEdit).not.toHaveBeenCalled();
  expect(host.textContent).toContain("修改预览");
  await click("应用并重新试跑");
  expect(updated.mock.lastCall?.[0]).toMatchObject({ id: "draft-multi", revision: 2 });
  expect(vi.mocked(studioClient.createTryRun).mock.lastCall?.slice(0, 3)).toEqual(["draft-multi", 2, "测试原始材料"]);
  vi.mocked(studioClient.converseBuilder).mockResolvedValue({ baseRevision: 2,
    reply: "再简短一点", changedFields: ["systemPrompt"], changes: { systemPrompt: "输出三行表格" } });
  await send("再缩短为三行");
  const request = vi.mocked(studioClient.converseBuilder).mock.lastCall![1];
  expect(request.expectedRevision).toBe(2);
  expect(request.messages.some((item) => item.content === "输出改成表格")).toBe(true);
  expect(request.messages.at(-1)?.content).toBe("再缩短为三行");
  act(() => toggleOpen(false)); act(() => toggleOpen(true));
  expect(host.textContent).toContain("修改预览");
  expect(host.textContent).toContain("再缩短为三行");
});

it("blocks applying a stale preview over manual unsaved edits and can discard it", async () => {
  await click("修改配置"); await send("输出改成表格");
  act(() => setDirty(true)); await click("应用修改");
  expect(studioClient.applyBuilderEdit).not.toHaveBeenCalled();
  expect(host.textContent).toContain("配置已有新变化");
  await click("放弃建议");
  expect(host.querySelector('[aria-label="待确认的配置修改"]')).toBeNull();
});

it("allows proposals while an older trial is running, but prevents concurrent reruns", async () => {
  const running = { ...run, run: { ...run.run, status: "running" } } as StudioTryRun;
  vi.mocked(studioClient.createTryRun).mockResolvedValue(running);
  vi.mocked(studioClient.getTryRun).mockResolvedValue(running);
  await click("试跑"); await send("分析材料");
  expect(host.querySelector('[aria-label="停止运行"]')).not.toBeNull();
  expect(host.querySelector('[aria-label="发送消息"]')).toBeNull();
  await click("修改配置"); await send("改成表格");
  expect(studioClient.converseBuilder).toHaveBeenCalledOnce();
  const rerun = [...host.querySelectorAll("button")].find((b) => b.textContent === "应用并重新试跑")!;
  expect(rerun.disabled).toBe(true);
  await click("应用修改");
  expect(studioClient.applyBuilderEdit).toHaveBeenCalledOnce();
  expect(host.textContent).toContain("当前试跑仍使用原配置");
});

it("continues a newly created agent's conversation and starts a fresh one only on explicit new", async () => {
  vi.spyOn(studioClient, "createDraftFromTask").mockResolvedValue({
    draft: api({ ...initial, id: "new-draft", builtinTools: [], mcpServers: [] }), recommendation: null,
  });
  act(() => newDraft());
  await send("创建报告助手");
  expect(host.textContent).toContain("已创建");
  expect(host.textContent).toContain("创建报告助手");
  expect(studioClient.createTryRun).not.toHaveBeenCalled();
  await click("修改配置"); await send("加入摘要");
  expect(vi.mocked(studioClient.converseBuilder).mock.lastCall?.[0]).toBe("new-draft");
  act(() => toggleOpen(false)); act(() => toggleOpen(true));
  expect(host.textContent).toContain("加入摘要");
  act(() => newDraft());
  expect(host.textContent).not.toContain("加入摘要");
  expect(host.querySelector("textarea")?.placeholder).toBe("描述你想创建的智能体…");
});


it("automatically routes edits, asks about ambiguity, and resumes with the original request in history", async () => {
  vi.mocked(studioClient.converseBuilder).mockResolvedValueOnce({ baseRevision: 1,
    action: "ask", reply: "是修改以后默认输出，还是只改这次结果？", changes: {}, changedFields: [] });
  await send("再短一点");
  expect(host.textContent).toContain("是修改以后默认输出");
  expect(studioClient.createTryRun).not.toHaveBeenCalled();
  expect(studioClient.applyBuilderEdit).not.toHaveBeenCalled();
  await send("修改以后默认输出，最多三行");
  const request = vi.mocked(studioClient.converseBuilder).mock.lastCall![1];
  expect(request.intent).toBe("auto");
  expect(request.messages.map((item) => item.content)).toEqual([
    "再短一点", "是修改以后默认输出，还是只改这次结果？", "修改以后默认输出，最多三行",
  ]);
  expect(host.textContent).toContain("修改预览");
  expect(studioClient.createTryRun).not.toHaveBeenCalled();
});

it("automatically runs a resolved task then reruns that task instead of the user's retry instruction", async () => {
  vi.mocked(studioClient.converseBuilder).mockResolvedValueOnce({ baseRevision: 1,
    action: "run", task: "分析材料 A 并输出摘要", reply: "开始测试", changes: {}, changedFields: [] });
  await send("测试一下：分析材料 A 并输出摘要");
  expect(vi.mocked(studioClient.createTryRun).mock.lastCall?.[2]).toBe("分析材料 A 并输出摘要");
  vi.mocked(studioClient.converseBuilder).mockResolvedValueOnce({ baseRevision: 1,
    action: "rerun", reply: "使用当前配置重新测试", changes: {}, changedFields: [] });
  await send("再跑一次");
  expect(studioClient.createTryRun).toHaveBeenCalledTimes(2);
  expect(vi.mocked(studioClient.createTryRun).mock.lastCall?.[2]).toBe("分析材料 A 并输出摘要");
  const request = vi.mocked(studioClient.converseBuilder).mock.lastCall![1];
  expect(JSON.parse(request.runContext).output).toBe("试跑结果");
  expect(JSON.parse(request.runContext).task).toBe("分析材料 A 并输出摘要");
});

it("does not run when clarification fails, a task is missing, or an unapplied proposal exists", async () => {
  vi.mocked(studioClient.converseBuilder).mockRejectedValueOnce(new Error("识别失败"));
  await send("不太对");
  expect(document.querySelector('[aria-label="操作提示"]')?.textContent).toContain("识别失败");
  vi.mocked(studioClient.converseBuilder).mockResolvedValueOnce({ baseRevision: 1,
    action: "rerun", reply: "再试一次", changes: {}, changedFields: [] });
  await send("再试一次");
  expect(document.querySelector('[aria-label="操作提示"]')?.textContent).toContain("还没有可执行的测试任务");
  await send("修改默认输出为表格");
  vi.mocked(studioClient.converseBuilder).mockResolvedValueOnce({ baseRevision: 1,
    action: "run", task: "测试材料", reply: "开始测试", changes: {}, changedFields: [] });
  await send("测试材料");
  expect(document.querySelector('[aria-label="操作提示"]')?.textContent).toContain("请先应用或放弃当前修改建议");
  expect(host.textContent).toContain("修改预览");
  expect(studioClient.createTryRun).not.toHaveBeenCalled();
});

it("protects Chinese composition Enter from sending and uses the main composer styling", async () => {
  const input = host.querySelector('[aria-label="智能体构建助手"] textarea:not([aria-label="对话预览输入"])') as HTMLTextAreaElement;
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(input, "修改提示词");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  act(() => {
    input.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true }));
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    input.dispatchEvent(new CompositionEvent("compositionend", { bubbles: true }));
    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  });
  expect(studioClient.converseBuilder).not.toHaveBeenCalled();
  expect(input.value).toBe("修改提示词");
  expect(input.closest(".harness-composer-shell .aui-composer-root")).not.toBeNull();
});

async function previewSend(value: string) {
  await click("试跑");
  await send(value);
}

it("sends preview text directly and continues the previous turn, resetting after configuration changes", async () => {
  await click("试跑"); await send("第一轮材料");
  await previewSend("它的结论是什么？");
  expect(studioClient.converseBuilder).not.toHaveBeenCalled();
  expect(vi.mocked(studioClient.createTryRun).mock.lastCall?.[2]).toBe("它的结论是什么？");
  expect(vi.mocked(studioClient.createTryRun).mock.lastCall?.[4]).toEqual({ continueFromRunId: "run-1" });
  expect(host.textContent).toContain("第一轮材料");
  await click("修改配置"); await send("换成表格"); await click("应用修改");
  await previewSend("使用新配置测试");
  expect(vi.mocked(studioClient.createTryRun).mock.lastCall?.[1]).toBe(2);
  expect(vi.mocked(studioClient.createTryRun).mock.lastCall?.[4]).toEqual({});
  await act(async () => { (host.querySelector('[aria-label="新测试会话"]') as HTMLButtonElement).click(); });
  expect(host.textContent).toContain("已开启新的测试会话");
  await previewSend("新的独立测试");
  expect(vi.mocked(studioClient.createTryRun).mock.lastCall?.[4]).toEqual({});
  expect(host.querySelectorAll("textarea")).toHaveLength(1);
});
it("keeps failed preview input retryable and supplies the selected turn as builder context", async () => {
  await click("试跑"); await send("选择这轮材料");
  vi.mocked(studioClient.createTryRun).mockRejectedValueOnce(new Error("网络中断"));
  await previewSend("保留我的输入");
  expect((host.querySelector('textarea') as HTMLTextAreaElement).value).toBe("保留我的输入");
  expect(host.textContent).toContain("选择这轮材料");
  await click("修改配置");
  await send("请修正这次回答缺少引用的问题");
  const context = JSON.parse(vi.mocked(studioClient.converseBuilder).mock.lastCall![1].runContext);
  expect(context.task).toBe("选择这轮材料");
  expect(context.runId).toBe("run-1");
  expect(context.output).toBe("试跑结果");
});

it("reruns the latest example when applying a configuration change", async () => {
  await click("试跑"); await send("旧案例 A");
  await previewSend("后续案例 B");
  await click("修改配置");
  await send("修改系统提示词，改进案例 B 的配置");
  await click("应用并重新试跑");
  expect(vi.mocked(studioClient.createTryRun).mock.lastCall?.[2]).toBe("后续案例 B");
  expect(vi.mocked(studioClient.createTryRun).mock.lastCall?.[4]).toEqual({});
});

function modelRuns(task: string) {
  vi.mocked(studioClient.converseBuilder).mockResolvedValueOnce({baseRevision:1, action:"run", task, reply:"开始处理", changedFields:[], changes:{}});
}
async function sendTest(value: string) {
  await act(async () => {
    const input = host.querySelector('[aria-label="智能体效果测试"] [aria-label="消息输入"]')!;
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => { (host.querySelector('[aria-label="智能体效果测试"] [aria-label="发送消息"]') as HTMLButtonElement).click(); });
}
it("uses one composer to edit and run without losing task context", async () => {
  act(() => enableWorkspace());
  expect(host.querySelectorAll("textarea")).toHaveLength(1);
  expect(host.querySelector('[aria-label="智能体资产"]')).toBeNull();
  expect(host.querySelector('[aria-label="智能体构建助手"]')).toBeNull();
  modelRuns("业务原始问题"); await sendTest("业务原始问题"); modelRuns("继续追问"); await sendTest("继续追问");
  expect(vi.mocked(studioClient.createTryRun).mock.lastCall?.[4]).toEqual({continueFromRunId:"run-1"});
  await sendTest("修改系统提示词，输出改成表格");
  expect(JSON.parse(vi.mocked(studioClient.converseBuilder).mock.lastCall![1].runContext).task).toBe("继续追问");
  expect(host.querySelector('[aria-label="待确认的配置修改"]')).not.toBeNull();
  await click("应用修改");
  expect(updated.mock.lastCall?.[0].revision).toBe(2);
  modelRuns("新配置测试"); await sendTest("新配置测试");
  expect(vi.mocked(studioClient.createTryRun).mock.lastCall?.[1]).toBe(2);
  expect(vi.mocked(studioClient.createTryRun).mock.lastCall?.[4]).toEqual({});
  expect(host.textContent).toContain("业务原始问题");
  expect(host.querySelectorAll("textarea")).toHaveLength(1);
  await startNewConversation(); modelRuns("独立案例"); await sendTest("独立案例");
  expect(vi.mocked(studioClient.createTryRun).mock.lastCall?.[4]).toEqual({});
});

it("preserves attachments and text after failed unified sends, and supplies files to both edit and run", async () => {
  act(() => enableWorkspace());
  vi.spyOn(globalThis,"fetch").mockImplementation(async(url)=>String(url).endsWith("/limits") ? Response.json({max_file_bytes:52428800,max_files:10,max_total_bytes:104857600}) : new Response(JSON.stringify({input_artifact_id:"input_artifact_example",name:"材料.txt",media_type:"text/plain",status:"ready",size_bytes:8}),{status:200}));
  async function attach() {
    await act(async()=>{
      const event = new Event("paste",{bubbles:true,cancelable:true});
      Object.defineProperty(event,"clipboardData",{value:{files:[new File(["原始材料"],"材料.txt",{type:"text/plain"})]}});
      host.querySelector('[aria-label="消息输入"]')!.dispatchEvent(event);
    });
  }
  await attach();
  vi.mocked(studioClient.readBuilderMaterials).mockRejectedValueOnce(new Error("读取失败"));
  await send("根据附件修改系统提示词中的输出格式");
  expect(host.querySelector<HTMLTextAreaElement>('[aria-label="消息输入"]')!.value).toContain("根据附件修改系统提示词中的输出格式");
  expect(host.querySelector('.harness-composer-shell .composer-file-card')?.textContent).toContain("材料.txt");
  await send("根据附件修改系统提示词中的输出格式");
  expect(vi.mocked(studioClient.converseBuilder).mock.lastCall?.[1].messages.at(-1)?.content).toContain("参考材料正文");
  await click("放弃建议"); await attach();
  vi.mocked(studioClient.createTryRun).mockRejectedValueOnce(new Error("临时中断"));
  modelRuns("分析附件"); await sendTest("分析附件");
  expect(host.querySelector('.harness-composer-shell .composer-file-card')?.textContent).toContain("材料.txt");
  modelRuns("分析附件"); await sendTest("分析附件");
  expect(vi.mocked(studioClient.createTryRun).mock.lastCall?.[4]).toMatchObject({inputArtifactIds:["input_artifact_example"]});
});

it("collapses configuration when opening files and preserves the conversation", async()=>{
  await act(async()=>enableWorkspace());
  await click("查看对话文件");
  expect(host.querySelector('[aria-label="智能体结构"]')).toBeNull();
  expect(host.querySelector('[aria-label="展开配置栏"]')).not.toBeNull();
  expect(host.querySelector(".workbench-rail")).not.toBeNull();
  expect(host.querySelector('[aria-label="文件目录"]')).toBeNull();
  expect(host.querySelector('[aria-label="消息输入"]')).not.toBeNull();
  expect(host.querySelector('[aria-label="智能体资产"]')).toBeNull();
  await click("收起任务上下文");
  expect(host.querySelector(".workbench-rail")).toBeNull();
});

it("saves configuration without waiting for a full project export", async () => {
  vi.mocked(studioClient.previewBuilderProjectDiff).mockImplementation(() => new Promise(() => {}));
  act(() => enableWorkspace());
  await send("修改系统提示词，输出改成表格");
  await click("应用修改");
  expect(studioClient.previewBuilderProjectDiff).not.toHaveBeenCalled();
  expect(studioClient.applyBuilderEdit).toHaveBeenCalledWith("draft-multi", {expectedRevision: 1, changes: {systemPrompt: "输出表格"}});
  expect(host.querySelector('[aria-label="测试代码差异"]')).toBeNull();
  expect(host.querySelector('[aria-label="消息输入"]')).not.toBeNull();
  expect(updated.mock.lastCall?.[0].revision).toBe(2);
});

it("exports only on explicit preview and reuses that comparison when applying", async () => {
  const source = { revision: 1, filename: "agent.zip", digest: "a", framework_version: "0.7.13", files: [] };
  vi.mocked(studioClient.previewBuilderProjectDiff).mockResolvedValue({before: source, after: {...source, revision: 2}});
  await act(async () => enableWorkspace());
  await send("修改系统提示词，输出改成表格");
  await click("查看代码差异 ↗");
  expect(studioClient.previewBuilderProjectDiff).toHaveBeenCalledTimes(1);
  expect(host.querySelector('[aria-label="测试代码差异"]')?.textContent).toContain("待应用");
  await click("应用修改");
  expect(studioClient.previewBuilderProjectDiff).toHaveBeenCalledTimes(1);
  expect(studioClient.applyBuilderEdit).toHaveBeenCalledTimes(1);
  expect(host.querySelector('[aria-label="测试代码差异"]')?.textContent).toContain("已应用");
});

it("creates from the actual brief and reviews recommended Skills before installation", async () => {
  vi.spyOn(studioClient, "createDraftFromTask").mockResolvedValue({draft: api(initial), recommendation: {
    generatedByModel: true, capabilityCatalogRevision: 7,
    recommendedSkills: [{packageId: "evidence-reporting", revision: 1, label: "证据报告", reason: "当前任务需要来源核验", risk: "low"}],
    runtime: initial.runtime, modelRouteId: initial.modelRoute, model: initial.model,
    template: initial.template, builtinTools: [], mcpServers: [], permissionPolicy: initial.policy,
    executionProfile: initial.executionProfile, reasons: [], validation: {ready: true, issues: [], productionEligible: true, contentHash: null, packageHash: null, runtimeCompatibility: {runtime: initial.runtime, label: "Worker", stability: "stable", compatible: true, capabilities: [], limitations: []}},
  }});
  act(() => newDraft());
  await send("联网核验公开资料并生成报告");
  expect(studioClient.createDraftFromTask).toHaveBeenCalledWith({task: "联网核验公开资料并生成报告", runtimePreference: "auto"}, expect.any(Function), expect.any(AbortSignal));
  expect(host.textContent).toContain("当前任务需要来源核验");
  expect(studioClient.applyBuilderEdit).not.toHaveBeenCalled();
  await act(async () => { host.querySelector<HTMLInputElement>('[aria-label="推荐 Skill"] input')!.click(); });
  await click("审阅所选 Skill");
  expect(host.textContent).toContain("修改预览");
  expect(studioClient.applyBuilderEdit).not.toHaveBeenCalled();
  await click("应用修改");
  expect(studioClient.applyBuilderEdit).toHaveBeenCalledWith(initial.id, {expectedRevision: 1, changes: {
    installSkills: [{packageId: "evidence-reporting", revision: 1}], capabilityCatalogRevision: 7,
  }});
});

it("renders model deltas before the completed proposal and keeps changes reviewable", async () => {
  let finish: (reply: Awaited<ReturnType<typeof studioClient.converseBuilder>>) => void;
  vi.mocked(studioClient.converseBuilder).mockImplementation(async (_id, _body, progress) => {
    expect(host.querySelector(".aui-composer-root")?.textContent).not.toContain("正在读取参考材料");
    expect(studioClient.readBuilderMaterials).not.toHaveBeenCalled();
    progress?.({ type: "builder.reply", text: "正在逐步输出建议" });
    return new Promise(resolve => { finish = resolve; });
  });
  await click("修改配置");
  await send("改成表格");
  // The reply is progressively revealed; it must appear while the proposal is pending.
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 100)); });
  expect(host.textContent).toContain("正在逐步输出建议");
  expect(host.querySelector('[aria-label="待确认的配置修改"]')).toBeNull();
  await act(async () => finish!({ baseRevision: 1, reply: "建议完成", changedFields: ["systemPrompt"], changes: { systemPrompt: "表格" } }));
  expect(host.textContent).toContain("建议完成");
  expect(host.querySelector('[aria-label="待确认的配置修改"]')).not.toBeNull();
  expect(studioClient.applyBuilderEdit).not.toHaveBeenCalled();
});

it("starts an empty test conversation and can resume a chosen history with its latest turn", async () => {
  act(() => enableWorkspace());
  modelRuns("第一组问题"); await sendTest("第一组问题"); modelRuns("第一组追问"); await sendTest("第一组追问");
  const panel = () => host.querySelector('[aria-label="智能体效果测试"]')!;
  expect(panel().querySelectorAll("[data-test-run]")).toHaveLength(2);
  expect(panel().textContent).toContain("当前对话 2 轮");
  await act(async () => {
    const input = panel().querySelector("textarea")!;
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(input, "未发送内容");
    input.dispatchEvent(new Event("input", {bubbles:true}));
  });
  await startNewConversation();
  expect(panel().querySelectorAll("[data-test-run]")).toHaveLength(0);
  expect(panel().querySelector("textarea")!.value).toBe("");
  expect(panel().textContent).toContain("帮你做些什么？");
  modelRuns("第二组独立问题"); await sendTest("第二组独立问题");
  expect(vi.mocked(studioClient.createTryRun).mock.lastCall?.[4]).toEqual({});
  expect(panel().querySelectorAll("[data-test-run]")).toHaveLength(1);
  await act(async () => {
    (panel().querySelector('[aria-label="会话：新建或切换"]') as HTMLElement).click();
  });
  await act(async () => {
    const menu = panel().querySelector('[aria-label="会话：新建或切换"]')!.closest("details")!;
    const row = [...menu.querySelectorAll("button")].find((item) => /第一组/.test(item.textContent ?? ""));
    row!.click();
  });
  expect(panel().querySelectorAll("[data-test-run]")).toHaveLength(2);
  modelRuns("回到第一组继续追问"); await sendTest("回到第一组继续追问");
  expect(vi.mocked(studioClient.createTryRun).mock.lastCall?.[4]).toEqual({continueFromRunId:"run-2"});
  expect(panel().querySelectorAll("[data-test-run]")).toHaveLength(3);
});


it("preserves unfinished test input when switching Build and Chat", async () => {
  await act(async () => enableWorkspace());
  const input=host.querySelector<HTMLTextAreaElement>('[aria-label="智能体效果测试"] [aria-label="消息输入"]')!;
  await act(async()=>{Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,"value")!.set!.call(input,"保留这段输入");input.dispatchEvent(new Event("input",{bubbles:true}));});
  await act(async()=>switchMode("chat"));
  expect(host.querySelector('[aria-label="历史会话"]')).toBeNull();
  expect(host.querySelector<HTMLTextAreaElement>('[aria-label="智能体效果测试"] [aria-label="消息输入"]')?.value).toBe("保留这段输入");
  await act(async()=>switchMode("build"));
  expect(host.querySelector<HTMLTextAreaElement>('[aria-label="智能体效果测试"] [aria-label="消息输入"]')?.value).toBe("保留这段输入");
});
it("restores persisted turns in chronological order and continues the latest revision", async()=>{
  vi.mocked(studioClient.listTryRuns).mockResolvedValue([
    {draftRevision:1,run:{...run.run,run_id:"saved-2",session_id:"saved-session",input:{prompt:"第二轮"},created_at:"2026-09-21T02:00:00Z"}},
    {draftRevision:1,run:{...run.run,run_id:"saved-1",session_id:"saved-session",input:{prompt:"第一轮"},created_at:"2026-09-21T01:00:00Z"}},
  ]);
  // Reset the component so it fetches the persisted index as a new visit would.
  await act(async()=>newDraft());
  vi.spyOn(studioClient,"createDraftFromTask").mockResolvedValue({draft:api(initial),recommendation:null} as never);
  await send("创建一个智能体");
  await act(async()=>{enableWorkspace();switchMode("chat");});
  const sessionMenu=host.querySelector('[aria-label="会话：新建或切换"]');
  expect(sessionMenu).not.toBeNull();
  vi.mocked(studioClient.getTryRun).mockImplementation(async(_id,revision,id)=>({...run,draftRevision:revision,run:{...run.run,run_id:id,session_id:"saved-session"},finalText:`回答 ${id}`}));
  await act(async()=>{(sessionMenu as HTMLElement).click();});
  const sessionRow=[...host.querySelectorAll("button")].find(item=>/对话 1/.test(item.textContent ?? ""));
  expect(sessionRow).toBeTruthy();
  await act(async()=>{sessionRow!.click();});
  const prompts=[...host.querySelectorAll('[data-test-run]')].map(item=>item.textContent);
  expect(prompts[0]).toContain("回答 saved-1");expect(prompts[1]).toContain("回答 saved-2");
  modelRuns("第三轮"); await sendTest("第三轮");
  expect(vi.mocked(studioClient.createTryRun).mock.lastCall?.[4]).toMatchObject({continueFromRunId:"saved-2"});
});

it("runs an explicit trial request directly while preserving the shared message", async () => {
  await act(async () => enableWorkspace());
  await send("试跑智能体：分析这段材料");
  expect(studioClient.converseBuilder).not.toHaveBeenCalled();
  expect(vi.mocked(studioClient.createTryRun).mock.lastCall?.[2]).toBe("试跑智能体：分析这段材料");
  expect(host.querySelectorAll("textarea")).toHaveLength(1);
});

it("lets the model answer a greeting without starting a trial", async () => {
  await act(async () => enableWorkspace());
  vi.mocked(studioClient.converseBuilder).mockResolvedValueOnce({baseRevision:1,action:"reply",reply:"你好，需要调整什么？",changedFields:[],changes:{}});
  await send("你好");
  expect(vi.mocked(studioClient.converseBuilder).mock.lastCall?.[1].intent).toBe("auto");
  expect(studioClient.createTryRun).not.toHaveBeenCalled();
  expect(host.textContent).toContain("你好，需要调整什么？");
});

it("replaces builder progress with one answer without retaining a false branch", async () => {
  let finish!: (reply: Awaited<ReturnType<typeof studioClient.converseBuilder>>) => void;
  vi.mocked(studioClient.converseBuilder).mockImplementation(() => new Promise(resolve => { finish = resolve; }));
  await act(async () => enableWorkspace());
  await send("修改系统提示词，以后回答简短一点");
  expect(studioClient.converseBuilder).toHaveBeenCalledTimes(1);
  await act(async () => finish({baseRevision:1, action:"reply", reply:"请说明希望保留哪些审查规则。", changedFields:[], changes:{}}));
  expect(host.querySelectorAll('.harness-branch-picker')).toHaveLength(0);
  expect(host.querySelector('[data-turn-answer="请说明希望保留哪些审查规则。"]')).not.toBeNull();
  expect(host.querySelectorAll('.harness-assistant-message')).toHaveLength(1);
  expect(studioClient.converseBuilder).toHaveBeenCalledTimes(1);
  expect(studioClient.createTryRun).not.toHaveBeenCalled();
});

it("keeps streamed builder text in one message and separates processing status", async () => {
  let emit: NonNullable<Parameters<typeof studioClient.converseBuilder>[2]>;
  let finish: (reply: Awaited<ReturnType<typeof studioClient.converseBuilder>>) => void;
  vi.mocked(studioClient.converseBuilder).mockImplementation(async (_id, _body, progress) => {
    emit = progress!;
    return new Promise(resolve => { finish = resolve; });
  });
  await act(async () => enableWorkspace());
  await send("请修改系统提示词，输出改成表格");
  await act(async () => emit!({type: "progress", text: "正在检查配置…"}));
  expect(host.querySelector('[role="status"]')?.textContent).toContain("正在检查配置");
  expect(host.querySelector('.assistant-answer')?.textContent ?? "").not.toContain("正在检查配置");
  await act(async () => emit!({type: "builder.reply", text: "建议输出"}));
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 100)); });
  const answer = host.querySelector('.harness-assistant-message');
  expect(answer?.textContent).toContain("建议输出");
  expect(answer?.querySelector(".execution-ribbon.phase-running")).not.toBeNull();
  expect(answer?.querySelector('.execution-state-sweep[data-running="true"]')).not.toBeNull();
  expect(answer?.textContent?.indexOf("正在检查配置")).toBeLessThan(answer?.textContent?.indexOf("建议输出") ?? 0);
  await act(async () => emit!({type: "builder.reply", text: "建议输出表格"}));
  expect(host.querySelector('.harness-assistant-message')).toBe(answer);
  await act(async () => finish!({baseRevision: 1, reply: "建议输出表格", changes: {}, changedFields: []}));
  expect(host.querySelector('.harness-assistant-message')).toBe(answer);
  expect(host.textContent).not.toContain("正在检查配置…");
  expect(answer?.querySelector(".execution-ribbon.phase-completed")).not.toBeNull();
  expect(answer?.querySelector(".execution-details-trigger")).toBeNull();
  expect(answer?.querySelector('[data-running="true"]')).toBeNull();
});


it("reviews selected and edited configuration changes above the common composer", async () => {
  vi.mocked(studioClient.converseBuilder).mockResolvedValue({baseRevision: 1, reply: "建议修改名称和提示词", changedFields: ["displayName", "systemPrompt"], changes: {displayName: "新名称", systemPrompt: "原建议", capabilityCatalogRevision: 7}});
  await act(async () => enableWorkspace());
  await sendTest("修改名称和系统提示词");
  const card = host.querySelector('[aria-label="待确认的配置修改"]')!;
  expect(card.closest(".harness-composer-shell")).not.toBeNull();
  expect(host.querySelectorAll('[aria-label="待确认的配置修改"]')).toHaveLength(1);
  await click("选择显示名称");
  await click("编辑系统提示词");
  await act(async () => {
    const input = card.querySelector('textarea[aria-label="系统提示词"]')!;
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(input, "由用户修改的提示词");
    input.dispatchEvent(new Event("input", {bubbles: true}));
  });
  await click("应用修改");
  const expected = {expectedRevision: 1, changes: {systemPrompt: "由用户修改的提示词", capabilityCatalogRevision: 7}};
  expect(studioClient.previewBuilderProjectDiff).not.toHaveBeenCalled();
  expect(studioClient.applyBuilderEdit).toHaveBeenCalledWith("draft-multi", expected);
});

it("blocks empty selections and retains review choices after an apply failure", async () => {
  await act(async () => enableWorkspace());
  await sendTest("修改系统提示词");
  await click("选择系统提示词");
  await click("应用修改");
  expect(studioClient.applyBuilderEdit).not.toHaveBeenCalled();
  await click("选择系统提示词");
  vi.mocked(studioClient.applyBuilderEdit).mockRejectedValueOnce(new Error("服务暂时不可用"));
  await click("应用修改");
  expect(host.querySelector<HTMLInputElement>('[aria-label="选择系统提示词"]')?.checked).toBe(true);
  expect(document.querySelector('[aria-label="操作提示"]')?.textContent).toContain("服务暂时不可用");
  await click("应用修改");
  expect(studioClient.applyBuilderEdit).toHaveBeenCalledTimes(2);
  expect(host.querySelector('[aria-label="待确认的配置修改"]')).toBeNull();
});

it("passes user-reviewed changes into the next Builder request and resets selection for a fresh proposal", async () => {
  await act(async () => enableWorkspace());
  await sendTest("修改系统提示词");
  await click("选择系统提示词");
  await sendTest("同时修改名称");
  expect(JSON.parse(vi.mocked(studioClient.converseBuilder).mock.lastCall![1].runContext).pendingProposal.changes).toEqual({});
  expect(host.querySelector<HTMLInputElement>('[aria-label="选择系统提示词"]')?.checked).toBe(true);
});

it("shows one primary composer action as an active run gains or loses follow-up text", async () => {
  let finish!: (reply: Awaited<ReturnType<typeof studioClient.converseBuilder>>) => void;
  vi.mocked(studioClient.converseBuilder).mockImplementation(async () => new Promise(resolve => { finish = resolve; }));
  await act(async () => enableWorkspace());
  await send("启用联网");
  const footer = () => host.querySelector(".composer-footer")!;
  expect(footer().querySelectorAll('[aria-label="停止运行"]')).toHaveLength(1);
  const input = host.querySelector<HTMLTextAreaElement>('[aria-label="消息输入"]')!;
  const setText = async (text: string) => act(async () => {
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(input, text);
    input.dispatchEvent(new Event("input", {bubbles: true}));
  });
  await setText("再补充一句");
  expect(footer().querySelector('[aria-label="停止运行"]')).toBeNull();
  expect(footer().querySelectorAll('[aria-label="加入队列"]')).toHaveLength(1);
  await setText("");
  expect(footer().querySelectorAll('[aria-label="停止运行"]')).toHaveLength(1);
  expect(footer().querySelector('[aria-label="加入队列"]')).toBeNull();
  await act(async () => finish({baseRevision: 1, reply: "无需修改", changedFields: [], changes: {}}));
});

it("loads six recent persisted turns, fetches older turns on demand and reuses cached results", async () => {
  const saved = Array.from({length: 14}, (_, i) => {
    const n = i + 1;
    return {draftRevision:1, run:{...run.run,run_id:`paged-${n}`,session_id:"paged-session",input:{prompt:`历史第 ${n} 轮`},created_at:`2026-09-21T${String(n).padStart(2,"0")}:00:00Z`}};
  });
  vi.mocked(studioClient.listTryRuns).mockResolvedValue(saved.reverse());
  await act(async () => newDraft());
  vi.spyOn(studioClient,"createDraftFromTask").mockResolvedValue({draft:api(initial),recommendation:null} as never);
  await send("创建一个智能体");
  await act(async () => {enableWorkspace();switchMode("chat");});
  vi.mocked(studioClient.getTryRun).mockImplementation(async (_id, revision, id) => ({...run,draftRevision:revision,run:{...run.run,run_id:id,session_id:"paged-session"},finalText:`回答 ${id}`}));
  await act(async () => (host.querySelector('[aria-label="会话：新建或切换"]') as HTMLElement).click());
  await act(async () => [...host.querySelectorAll<HTMLButtonElement>("button")].find(b => /对话 1/.test(b.textContent ?? ""))!.click());
  expect(studioClient.getTryRun).toHaveBeenCalledTimes(6);
  expect(host.querySelectorAll('[data-test-run]')).toHaveLength(6);
  expect(host.textContent).toContain("回答 paged-9");
  expect(host.textContent).not.toContain("回答 paged-8");
  await click("查看更早的消息");
  expect(studioClient.getTryRun).toHaveBeenCalledTimes(12);
  expect(host.querySelectorAll('[data-test-run]')).toHaveLength(12);
  await click("查看更早的消息");
  expect(studioClient.getTryRun).toHaveBeenCalledTimes(14);
  expect(host.querySelectorAll('[data-test-run]')).toHaveLength(14);
  expect([...host.querySelectorAll("button")].some(b => b.textContent === "查看更早的消息")).toBe(false);
  modelRuns("继续当前会话"); await sendTest("继续当前会话");
  expect(vi.mocked(studioClient.createTryRun).mock.lastCall?.[4]).toMatchObject({continueFromRunId:"paged-14"});
});

it("routes conversational skill removal through the model and keeps it reviewable", async () => {
  await act(async () => enableWorkspace());
  vi.mocked(studioClient.converseBuilder).mockResolvedValueOnce({baseRevision:1,action:"edit",reply:"从此草稿移除 archify",changedFields:["skills"],changes:{removeSkills:["archify"]}});
  await send("archify 先别用了");
  expect(vi.mocked(studioClient.converseBuilder).mock.lastCall?.[1].intent).toBe("auto");
  expect(host.querySelector('[aria-label="待确认的配置修改"]')).not.toBeNull();
  expect(studioClient.applyBuilderEdit).not.toHaveBeenCalled();
  expect(studioClient.createTryRun).not.toHaveBeenCalled();
});
it("clears a pending edit only when the model explicitly replaces it with an empty edit", async () => {
  await act(async () => enableWorkspace());
  await send("输出改成表格");
  vi.mocked(studioClient.converseBuilder).mockResolvedValueOnce({baseRevision:1,action:"reply",reply:"需要保留吗？",changedFields:[],changes:{}});
  await send("先解释一下");
  expect(host.querySelector('[aria-label="待确认的配置修改"]')).not.toBeNull();
  vi.mocked(studioClient.converseBuilder).mockResolvedValueOnce({baseRevision:1,action:"edit",reply:"已取消待应用建议",changedFields:[],changes:{}});
  await send("刚才那个不要改了");
  expect(host.querySelector('[aria-label="待确认的配置修改"]')).toBeNull();
  expect(studioClient.applyBuilderEdit).not.toHaveBeenCalled();
});
