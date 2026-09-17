// @vitest-environment jsdom
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AgentBuilderAssistant } from "../src/components/agent-studio/agent-builder-overlays";
import { DEFAULT_STUDIO_DRAFT, type StudioDraft } from "../src/lib/agent-studio";
import { studioClient, studioDraftToSpec, type ApiAgentDraft, type StudioTryRun } from "../src/lib/studio-client";

vi.mock("../src/components/agent-studio/agent-project-code", () => ({ AgentProjectCode: ({comparison, comparisonPending}: {comparison?: {before:{revision:number};after:{revision:number}};comparisonPending:boolean}) => <section aria-label="测试代码差异">{comparison?.before.revision} → {comparison?.after.revision} · {comparisonPending ? "待应用" : "已应用"}</section> }));
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root;
let host: HTMLDivElement;
let toggleOpen: (value: boolean) => void;
let setDirty: (value: boolean) => void;
let newDraft: () => void;
let enableWorkspace: () => void;
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
  vi.stubGlobal("XMLHttpRequest", undefined);
  HTMLElement.prototype.scrollTo = vi.fn();
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
    const [embedded, setEmbedded] = useState(false);
    const [target, setTarget] = useState<HTMLDivElement | null>(null);
    enableWorkspace = () => setEmbedded(true);
    const [open, openChange] = useState(true);
    const [dirty, dirtyChange] = useState(false);
    const [mode, setMode] = useState<"create" | "run">("run");
    const [creationSession, setCreationSession] = useState(0);
    toggleOpen = openChange; setDirty = dirtyChange;
    newDraft = () => { setMode("create"); setCreationSession((value) => value + 1); };
    return <><div ref={setTarget} /><AgentBuilderAssistant workspaceTarget={embedded ? target : undefined} open={open} mode={mode} creationSession={creationSession} draft={draft} initialPrompt=""
      recommendation={null} knowledgeMcpReferences={[]} hasUnsavedChanges={dirty}
      onClose={() => openChange(false)} onCreated={(flow) => { setDraft(flow.draft); setMode("run"); }} prepareDraft={async () => draft}
      onUpdated={(next) => { updated(next); setDraft(next); }} /></>;
  }
  act(() => root.render(<Harness />));
});
afterEach(() => {
  vi.unstubAllGlobals(); act(() => root.unmount()); host.remove(); vi.restoreAllMocks(); updated.mockReset(); });
async function click(text: string) {
  await act(async () => { [...host.querySelectorAll("button")].find((button) => button.textContent === text)!.click(); });
}
async function send(value: string) {
  await act(async () => {
    const input = host.querySelector<HTMLTextAreaElement>('[aria-label="智能体构建助手"] textarea:not([aria-label="对话预览输入"])')!;
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
  expect(host.textContent).toContain("识别失败");
  vi.mocked(studioClient.converseBuilder).mockResolvedValueOnce({ baseRevision: 1,
    action: "rerun", reply: "再试一次", changes: {}, changedFields: [] });
  await send("再试一次");
  expect(host.textContent).toContain("还没有可执行的测试任务");
  await send("修改默认输出为表格");
  vi.mocked(studioClient.converseBuilder).mockResolvedValueOnce({ baseRevision: 1,
    action: "run", task: "测试材料", reply: "开始测试", changes: {}, changedFields: [] });
  await send("测试材料");
  expect(host.textContent).toContain("请先应用或放弃当前修改建议");
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
  await click("改进这次回答");
  await send("请修正这次回答缺少引用的问题");
  const context = JSON.parse(vi.mocked(studioClient.converseBuilder).mock.lastCall![1].runContext);
  expect(context.task).toBe("选择这轮材料");
  expect(context.runId).toBe("run-1");
  expect(context.output).toBe("试跑结果");
});

it("reruns the selected older example when applying its improvement", async () => {
  await click("试跑"); await send("旧案例 A");
  await previewSend("后续案例 B");
  await act(async () => { [...host.querySelectorAll("button")].find(button => button.textContent === "改进这次回答")!.click(); });
  await send("改进案例 A 的配置");
  await click("应用并重新试跑");
  expect(vi.mocked(studioClient.createTryRun).mock.lastCall?.[2]).toBe("旧案例 A");
  expect(vi.mocked(studioClient.createTryRun).mock.lastCall?.[4]).toEqual({});
});

async function sendTest(value: string) {
  await act(async () => {
    const input = host.querySelector('[aria-label="效果测试输入"]')!;
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => { (host.querySelector('[aria-label="发送测试消息"]') as HTMLButtonElement).click(); });
}
it("keeps build and test contexts separate and opens assets by default", async () => {
  act(() => enableWorkspace());
  expect(host.querySelector('[aria-label="智能体资产"]')).not.toBeNull();
  await act(async () => { (host.querySelector('[aria-label="收起智能体资产"]') as HTMLButtonElement).click(); });
  expect(host.querySelector('[aria-label="智能体资产"]')).toBeNull();
  await sendTest("业务原始问题"); await sendTest("继续追问");
  expect(studioClient.converseBuilder).not.toHaveBeenCalled();
  expect(vi.mocked(studioClient.createTryRun).mock.lastCall?.[4]).toEqual({continueFromRunId: "run-1"});
  expect(host.querySelector('[aria-label="智能体构建助手"]')?.textContent).not.toContain("试跑结果");
  expect(host.querySelector('[aria-label="智能体效果测试"]')?.textContent).toContain("试跑结果");
  await click("配置与文件");
  expect(host.querySelector('[aria-label="智能体资产"]')).not.toBeNull();
  await click("文件"); expect(host.textContent).toContain("尚无交付文件");
  await click("新对话"); await sendTest("独立案例");
  expect(vi.mocked(studioClient.createTryRun).mock.lastCall?.[4]).toEqual({});
});
it("links a test answer to build changes and shows the new revision before replaying", async () => {
  act(() => enableWorkspace()); await sendTest("需要改进的材料");
  await click("改进这次回答"); await send("输出改成表格");
  expect(JSON.parse(vi.mocked(studioClient.converseBuilder).mock.lastCall![1].runContext).task).toBe("需要改进的材料");
  await click("查看完整差异 ↗");
  expect(host.querySelector('[aria-label="智能体资产"]')?.textContent).toContain("修改前");
  await click("应用修改");
  expect(host.querySelector('[aria-label="智能体效果测试"]')?.textContent).toContain("配置已更新至 r2");
  await sendTest("新配置测试");
  expect(vi.mocked(studioClient.createTryRun).mock.lastCall?.[1]).toBe(2);
  expect(vi.mocked(studioClient.createTryRun).mock.lastCall?.[4]).toEqual({});
});

it("uploads from either workspace composer and retains failed test attachments for retry", async () => {
  act(() => enableWorkspace());
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify({input_artifact_id: "input_artifact_example", name: "材料.txt", media_type: "text/plain", status: "ready", size_bytes: 8}), {status: 200}));
  async function attach(label: string) {
    await act(async () => {
      const input = host.querySelector(`[aria-label="${label}"]`)!;
      Object.defineProperty(input, "files", {configurable: true, value: [new File(["原始材料"], "材料.txt", {type: "text/plain"})]});
      input.dispatchEvent(new Event("change", {bubbles: true}));
    });
  }
  await attach("添加构建附件");
  vi.mocked(studioClient.readBuilderMaterials).mockRejectedValueOnce(new Error("读取失败"));
  await send("根据附件修改格式");
  expect(host.querySelector('[aria-label="移除 材料.txt"]')).not.toBeNull();
  await send("根据附件修改格式");
  expect(studioClient.createTryRun).not.toHaveBeenCalled();
  expect(vi.mocked(studioClient.converseBuilder).mock.lastCall?.[1].messages.at(-1)?.content).toContain("参考材料正文");
  expect(host.querySelector('[aria-label="智能体效果测试"] footer')?.textContent).not.toContain("材料.txt");
  await click("放弃建议");
  await attach("添加测试附件");
  vi.mocked(studioClient.createTryRun).mockRejectedValueOnce(new Error("视觉模型不可用"));
  await sendTest("再测试一次");
  expect(host.querySelector('[aria-label="智能体效果测试"] footer [aria-label="移除 材料.txt"]')).not.toBeNull();
  await sendTest("再测试一次");
  expect(vi.mocked(studioClient.createTryRun).mock.lastCall?.[4]).toMatchObject({inputArtifactIds: ["input_artifact_example"]});
});

it("uses creation references on the left and previews images independently on the right", async () => {
  act(() => { enableWorkspace(); newDraft(); });
  vi.spyOn(studioClient, "createDraftFromTask").mockResolvedValue({draft: api({...initial, id: "new-draft", builtinTools: [], mcpServers: []}), recommendation: null});
  vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify({input_artifact_id: "input_artifact_image", name: "图片.png", media_type: "image/png", status: "ready", size_bytes: 8}), {status: 200}));
  const file = new File(["png"], "图片.png", {type: "image/png"});
  await act(async () => {
    const input = host.querySelector('[aria-label="添加构建附件"]')!;
    Object.defineProperty(input, "files", {value: [file]});
    input.dispatchEvent(new Event("change", {bubbles: true}));
  });
  await send("创建能识图的助手");
  expect(studioClient.createTryRun).not.toHaveBeenCalled();
  expect(host.querySelector('[aria-label="智能体效果测试"]')?.textContent).not.toContain("图片.png");
  expect(vi.mocked(studioClient.createDraftFromTask).mock.lastCall?.[0].sampleInput).toBe("参考材料正文");
  expect(host.querySelector('[aria-label="智能体构建助手"] img')?.getAttribute("src")).toContain("input_artifact_image/content");
  await sendTest("描述这张图片");
  expect(vi.mocked(studioClient.createTryRun).mock.lastCall?.[2]).toBe("描述这张图片");
  expect(vi.mocked(studioClient.createTryRun).mock.lastCall?.[4]).toEqual({});
  act(() => toggleOpen(false)); act(() => toggleOpen(true));
  expect(host.querySelector('[aria-label="智能体效果测试"] footer')?.textContent).not.toContain("图片.png");
  await act(async () => {
    const event = new Event("paste", {bubbles: true, cancelable: true});
    Object.defineProperty(event, "clipboardData", {value: {files: [file]}});
    host.querySelector('[aria-label="效果测试输入"]')!.dispatchEvent(event);
  });
  expect(host.querySelector('[aria-label="智能体效果测试"]')?.textContent).toContain("图片.png");
  await sendTest("再看看这张图");
  expect(vi.mocked(studioClient.createTryRun).mock.lastCall?.[4]).toMatchObject({inputArtifactIds: ["input_artifact_image"]});
});

it("captures the actual project comparison before apply and opens it after the revision changes", async () => {
  const source = { revision: 1, filename: "agent.zip", digest: "a", framework_version: "0.7.13", files: [] };
  vi.mocked(studioClient.previewBuilderProjectDiff).mockResolvedValue({before: source, after: {...source, revision: 2}});
  act(() => enableWorkspace());
  await send("输出改成表格");
  await click("应用修改");
  expect(studioClient.previewBuilderProjectDiff).toHaveBeenCalledWith("draft-multi", {expectedRevision: 1, changes: {systemPrompt: "输出表格"}});
  expect(vi.mocked(studioClient.previewBuilderProjectDiff).mock.invocationCallOrder[0]).toBeLessThan(vi.mocked(studioClient.applyBuilderEdit).mock.invocationCallOrder[0]);
  expect(host.textContent).toContain("1 → 2 · 已应用");
  expect(updated.mock.lastCall?.[0].revision).toBe(2);
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

  expect(host.textContent).toContain("正在逐步输出建议");
  expect(host.querySelector('[aria-label="待确认的配置修改"]')).toBeNull();
  await act(async () => finish!({ baseRevision: 1, reply: "建议完成", changedFields: ["systemPrompt"], changes: { systemPrompt: "表格" } }));
  expect(host.textContent).toContain("建议完成");
  expect(host.querySelector('[aria-label="待确认的配置修改"]')).not.toBeNull();
  expect(studioClient.applyBuilderEdit).not.toHaveBeenCalled();
});

it("starts an empty test conversation and can resume a chosen history with its latest turn", async () => {
  act(() => enableWorkspace());
  await sendTest("第一组问题"); await sendTest("第一组追问");
  const panel = () => host.querySelector('[aria-label="智能体效果测试"]')!;
  expect(panel().querySelectorAll("[data-test-run]")).toHaveLength(2);
  expect(panel().textContent).toContain("当前对话 2 轮");
  await act(async () => {
    const input = panel().querySelector("textarea")!;
    Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!.call(input, "未发送内容");
    input.dispatchEvent(new Event("input", {bubbles:true}));
  });
  await click("新对话");
  expect(panel().querySelectorAll("[data-test-run]")).toHaveLength(0);
  expect(panel().querySelector("textarea")!.value).toBe("");
  expect(panel().textContent).toContain("新对话，不携带其他对话上下文");
  await sendTest("第二组独立问题");
  expect(vi.mocked(studioClient.createTryRun).mock.lastCall?.[4]).toEqual({});
  expect(panel().querySelectorAll("[data-test-run]")).toHaveLength(1);
  await act(async () => {
    const select = panel().querySelector("select")!;
    select.value = "session-run-1";select.dispatchEvent(new Event("change",{bubbles:true}));
  });
  expect(panel().querySelectorAll("[data-test-run]")).toHaveLength(2);
  await sendTest("回到第一组继续追问");
  expect(vi.mocked(studioClient.createTryRun).mock.lastCall?.[4]).toEqual({continueFromRunId:"run-2"});
  expect(panel().querySelectorAll("[data-test-run]")).toHaveLength(3);
});
