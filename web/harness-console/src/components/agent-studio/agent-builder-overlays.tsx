"use client";

import { createPortal } from "react-dom";
import { WorkspaceAttachments, type WorkspaceFile } from "./workspace-attachments";
import { AgentTestPanel } from "./agent-test-panel";
import { AgentBuildAssets, type BuildChange } from "./agent-build-assets";
import workspaceStyles from "./build-workspace.module.css";
import { PreviewRunResponse, PreviewMarkdown, type PreviewTurn } from "./agent-preview";
import { useEffect, useMemo, useRef, useState } from "react";
import {
  apiDraftToStudioDraft,
  studioClient,
  type StudioTaskDrivenRecommendation,
  type StudioTryRun,
  type StudioBuilderReply,
} from "../../lib/studio-client";
import { createInputAttachmentAdapter, inputArtifactIdFromAttachment } from "../../lib/input-attachment-adapter";
import { createRandomId } from "../../lib/random-id";
import type { StudioDraft } from "../../lib/agent-studio";
import {
  appendTryRunEvent,
  mergeTryRunView,
  } from "./try-run-stream";
import { PanelResizeHandle } from "../panel-resize-handle";
import styles from "./agent-builder-overlays.module.css";

export type CreatedAgentFlow = {
  draft: StudioDraft;
  prompt: string;
  recommendation: StudioTaskDrivenRecommendation | null;
  autoRun: boolean;
};

type AssistantMode = "create" | "run";
type ConversationMessage = {
  id: string;
  role: "assistant" | "user";
  text: string;
  runId?: string;
  files?: string[];
  artifactIds?: string[];
  materialContext?: string;
  tone?: "success" | "danger" | "muted";
};

const editLabels: Record<string, string> = {
  displayName: "显示名称", description: "简介", systemPrompt: "系统提示词",
  taskContract: "任务与输出要求", builtinTools: "内置工具", mcpServers: "MCP",
  knowledgeReferences: "知识库", skillInstructions: "Skill 正文", removeSkills: "移除 Skill",
  roleResponsibilities: "协作角色职责",
};

function beforeEdit(draft: StudioDraft, key: string): unknown {
  if (key === "skillInstructions") return draft.skills.map(({ name, instructions }) => ({ name, instructions }));
  if (key === "removeSkills") return draft.skills.map(({ name }) => name);
  if (key === "roleResponsibilities") return draft.subagents.map(({ alias, responsibility }) => ({ alias, responsibility }));
  return draft[key as keyof StudioDraft];
}

function showValue(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value, null, 2) ?? "未设置";
}

function tryRunFailureMessage(errorCode?: string | null): string {
  if (errorCode === "runtime_error") {
    return "运行环境未能启动，请检查模型渠道配置后重新试跑。";
  }
  if (errorCode === "runtime_timeout") {
    return "试跑超时，运行已安全停止；可缩小任务范围后重试。";
  }
  return errorCode || "试跑未通过，请调整要求后再试。";
}

function initialMessages(mode: AssistantMode, draft: StudioDraft): ConversationMessage[] {
  return [{
    id: "welcome",
    role: "assistant",
    text: mode === "create"
      ? "描述你想创建的智能体。我会生成草稿，你可以继续修改，或在右侧测试效果。"
      : `正在处理 ${draft.displayName}。直接描述测试任务或修改要求，我会结合上下文判断；不明确时会追问，配置修改先展示预览。`,
  }];
}

export function AgentBuilderAssistant({
  open,
  mode,
  draft,
  initialPrompt,
  recommendation: initialRecommendation,
  onClose,
  onCreated,
  prepareDraft,
  knowledgeMcpReferences,
  hasUnsavedChanges,
  onUpdated,
  creationSession = 0,
  workspaceTarget, onEditConfiguration, testRequest = 0,
}: {
  open: boolean;
  mode: AssistantMode;
  draft: StudioDraft;
  initialPrompt: string;
  recommendation: StudioTaskDrivenRecommendation | null;
  onClose: () => void;
  onCreated: (flow: CreatedAgentFlow) => void;
  prepareDraft: () => Promise<StudioDraft | null>;
  knowledgeMcpReferences: string[];
  hasUnsavedChanges: boolean;
  onUpdated: (draft: StudioDraft) => void;
  creationSession?: number;
  workspaceTarget?: HTMLElement | null;
  onEditConfiguration?: (section?: "identity" | "prompt" | "skills" | "capabilities" | "runtime", label?: string) => void;
  testRequest?: number;
}) {
  const [input, setInput] = useState("");
  const [assetsOpen, setAssetsOpen] = useState(true);
  const [assetTab, setAssetTab] = useState<"config" | "changes">("config");
  const [mobilePanel, setMobilePanel] = useState<"build" | "test">("build");
  const [selectedRunId, setSelectedRunId] = useState("");
  const [lastChanges, setLastChanges] = useState<BuildChange[]>([]);
  const [attachments, setAttachments] = useState<WorkspaceFile[]>([]);
  const [readingMaterials, setReadingMaterials] = useState(false);
  const uploadLock = useRef(false);
  const submitLock = useRef(false);
  const [uploading, setUploading] = useState(false);
  const followOutput = useRef(true);
  const [messages, setMessages] = useState<ConversationMessage[]>(() => initialMessages(mode, draft));
  const [workingDraft, setWorkingDraft] = useState<StudioDraft | null>(null);
  const [recommendation, setRecommendation] = useState(initialRecommendation);
  const [result, setResult] = useState<StudioTryRun | null>(null);
  const [creating, setCreating] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [intent, setIntent] = useState<"auto" | "run" | "edit">("auto");
  const [editing, setEditing] = useState(false);
  const [applying, setApplying] = useState(false);
  const [proposal, setProposal] = useState<(StudioBuilderReply & { before: StudioDraft; testTurn?: PreviewTurn }) | null>(null);
  const [lastTestPrompt, setLastTestPrompt] = useState("");
  const [archivedTurns, setArchivedTurns] = useState<PreviewTurn[]>([]);
  const [currentFiles, setCurrentFiles] = useState<string[]>([]);
  const [lastArtifactIds, setLastArtifactIds] = useState<string[]>([]);
  const [feedbackTurn, setFeedbackTurn] = useState<PreviewTurn | null>(null);
  const startingRef = useRef(false);
  const sessionKeyRef = useRef("");
  const epochRef = useRef(0);
  const latestRef = useRef({ hasUnsavedChanges, onUpdated });
  latestRef.current = { hasUnsavedChanges, onUpdated };
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const composingRef = useRef(false);
  const compositionEndedAt = useRef(-Infinity);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const runStreamAbortRef = useRef<AbortController | null>(null);

  const activeDraft = workingDraft && workingDraft.id !== draft.id ? workingDraft : draft;
  const draftReady = mode === "run" ? Boolean(activeDraft.id) : Boolean(workingDraft?.id);
  const terminal = useMemo(
    () => result ? ["cancelled", "succeeded", "failed", "timed_out", "rejected"].includes(result.run.status) : false,
    [result],
  );
  const active = creating || busy || Boolean(result && !terminal);
  const inputBusy = readingMaterials || uploading || creating || busy || editing || applying || (intent === "run" && active);

  useEffect(() => {
    if (open) window.setTimeout(() => inputRef.current?.focus(), 0);
    const key = mode === "create" ? `create:${creationSession}:${draft.id}` : `run:${draft.id}`;
    if (sessionKeyRef.current === key) return;
    sessionKeyRef.current = key;
    epochRef.current += 1;
    runStreamAbortRef.current?.abort();
    startingRef.current = false;
    setIntent("auto");
    setEditing(false);
    setApplying(false);
    setProposal(null);
    setLastChanges([]); setSelectedRunId(""); setAssetsOpen(true); setMobilePanel("build");
    setLastTestPrompt("");
    setArchivedTurns([]); setCurrentFiles([]); setLastArtifactIds([]); setFeedbackTurn(null);
    setInput(initialPrompt);
    setMessages(initialMessages(mode, draft));
    setWorkingDraft(mode === "run" ? draft : null);
    setCreating(false);
    setBusy(false);
    setRecommendation(initialRecommendation);
    setResult(null);
    setError("");
    setAttachments([]); setUploading(false); setReadingMaterials(false); uploadLock.current = false; submitLock.current = false; followOutput.current = true;
    window.setTimeout(() => inputRef.current?.focus(), 0);
  // Closing/reopening and new revisions retain this draft's conversation.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, mode, draft.id, creationSession]);

  useEffect(() => { if (testRequest) { setMobilePanel("test"); window.setTimeout(() => workspaceTarget?.querySelector<HTMLTextAreaElement>('[aria-label="效果测试输入"]')?.focus(), 0); } }, [testRequest, workspaceTarget]);

  useEffect(() => () => {
    epochRef.current += 1;
    runStreamAbortRef.current?.abort();
  }, []);

  useEffect(() => {
    if (!result || terminal) return;
    const timer = window.setInterval(() => {
      void studioClient
        .getTryRun(activeDraft.id, result.draftRevision, result.run.run_id)
        .then((next) => setResult((current) => current?.run.run_id === next.run.run_id
          ? mergeTryRunView(current, next) : current))
        .catch((reason) => setError(reason instanceof Error ? reason.message : "运行状态刷新失败"));
    }, 1200);
    return () => window.clearInterval(timer);
  }, [result, terminal, activeDraft.id]);

  useEffect(() => {
    if (followOutput.current) transcriptRef.current?.scrollTo({ top: transcriptRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, result]);

  async function upload(files: File[]) {
    if (inputBusy || uploadLock.current || !files.length) return;
    uploadLock.current = true;
    const epoch = epochRef.current;
    setUploading(true); setError("");
    try {
      const adapter = createInputAttachmentAdapter();
      for (const file of files) {
        const iterator = adapter.add({ file });
        if (!(Symbol.asyncIterator in iterator)) throw new Error("附件上传不可用");
        for await (const pending of iterator) {
          if (pending.status.type !== "requires-action") continue;
          const attachment = await adapter.send(pending);
          const id = inputArtifactIdFromAttachment(attachment);
          if (id && epoch === epochRef.current) setAttachments(current => [...current, { id, name: file.name, mediaType: attachment.contentType }]);
        }
      }
    } catch (reason) { if (epoch === epochRef.current) setError(reason instanceof Error ? reason.message : "上传失败"); }
    finally { if (epoch === epochRef.current) {setUploading(false); uploadLock.current = false;} }
  }

  async function startRun(value: string, targetDraft?: StudioDraft, continueConversation = false, artifactIds: string[] = [], names: string[] = []): Promise<boolean> {
    if (startingRef.current || (result && !terminal)) return false;
    startingRef.current = true;
    const epoch = epochRef.current;
    setBusy(true);
    setError("");
    try {
      const runnableDraft = targetDraft ?? await prepareDraft();
      if (epoch !== epochRef.current || !runnableDraft?.id || !value.trim()) return false;
      const previous = continueConversation && result?.draftRevision === runnableDraft.revision ? result : null;
      const started = await studioClient.createTryRun(
        runnableDraft.id,
        runnableDraft.revision,
        value.trim(),
        `studio-try-${createRandomId()}`,
        { ...(previous ? { continueFromRunId: previous.run.run_id } : {}), ...(artifactIds.length ? { inputArtifactIds: artifactIds } : {}) },
      );
      if (epoch !== epochRef.current) return false;
      runStreamAbortRef.current?.abort();
      if (result && result.run.run_id !== started.run.run_id) setArchivedTurns(current => [...current, { prompt: lastTestPrompt, result, files: currentFiles, artifactIds: lastArtifactIds }]);
      setLastTestPrompt(value.trim());
      setCurrentFiles(names); setLastArtifactIds(artifactIds);
      setResult(started);
      setMobilePanel("test");
      setSelectedRunId("");
      setMessages(current => [...current, { id: `run-${started.run.run_id}`, role: "assistant", text: "", runId: started.run.run_id }]);
      const controller = new AbortController();
      runStreamAbortRef.current = controller;
      const afterSequence = started.events.at(-1)?.sequence ?? 0;
      void studioClient.streamTryRunEvents(
        runnableDraft.id,
        started.draftRevision,
        started.run.run_id,
        afterSequence,
        (event) => setResult((current) => current?.run.run_id === started.run.run_id
          ? appendTryRunEvent(current, event)
          : current),
        controller.signal,
      ).then(async () => {
        if (controller.signal.aborted) return;
        const finalView = await studioClient.getTryRun(
          runnableDraft.id,
          started.draftRevision,
          started.run.run_id,
        );
        if (!controller.signal.aborted) setResult((current) => current?.run.run_id === started.run.run_id
          ? mergeTryRunView(current, finalView) : current);
      }).catch((reason) => {
        if (controller.signal.aborted) return;
        setError(reason instanceof Error ? reason.message : "实时输出连接中断，正在继续刷新运行状态");
      });
      return true;
    } catch (reason) {
      if (epoch !== epochRef.current) return false;
      const message = reason instanceof Error ? reason.message : "试跑失败";
      setError(message);
      setMessages((current) => [...current, { id: createRandomId(), role: "assistant", tone: "danger", text: message }]);
      return false;
    } finally {
      if (epoch === epochRef.current) { startingRef.current = false; setBusy(false); }
    }
  }

  async function createDraft(value: string, materialContext = "") {
    setCreating(true);
    setError("");
    try {
      const task = [
        value.trim(),
        "",
        "当前部署约束：只使用 Worker 运行；不访问外部网络；优先使用已有知识库和工作区数据。",
      ].join("\n");
      const created = await studioClient.createDraftFromTask({ task, ...(materialContext ? {sampleInput: materialContext} : {}), runtimePreference: "auto" });
      const generatedDraft = apiDraftToStudioDraft(created.draft);
      const restrictedDraft = {
        ...generatedDraft,
        builtinTools: generatedDraft.builtinTools,
        mcpServers: generatedDraft.mcpServers.filter((reference) => knowledgeMcpReferences.includes(reference)),
      };
      const environmentRestricted = restrictedDraft.builtinTools.length !== generatedDraft.builtinTools.length
        || restrictedDraft.mcpServers.length !== generatedDraft.mcpServers.length;
      const nextDraft = environmentRestricted
        ? apiDraftToStudioDraft(await studioClient.replaceDraft(restrictedDraft))
        : generatedDraft;
      setWorkingDraft(nextDraft);
      sessionKeyRef.current = `run:${nextDraft.id}`;
      setRecommendation(created.recommendation);
      setMessages((current) => [...current, {
        id: createRandomId(),
        role: "assistant",
        tone: "success",
        text: `已创建“${nextDraft.displayName}”草稿。可以继续告诉我修改要求，或在右侧输入实际问题测试效果。${attachments.length ? "已将附件作为构建参考材料。" : ""}`,
      }]);
      onCreated({ draft: nextDraft, prompt: value.trim(), recommendation: created.recommendation, autoRun: false });
      setAttachments([]); setAssetsOpen(true);
      return true;
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : "创建失败";
      setError(message);
      setMessages((current) => [...current, { id: createRandomId(), role: "assistant", tone: "danger", text: message }]);
      return false;
    } finally {
      setCreating(false);
    }
  }

  async function send() {
    const value = input.trim();
    if (!value || inputBusy || submitLock.current) return;
    const sendAsTest = intent === "run";
    if (sendAsTest && proposal) { setError("请先应用或放弃当前修改建议，再开始试跑。"); return; }
    submitLock.current = true;
    const epoch = epochRef.current;
    const messageId = createRandomId();
    try {
      setReadingMaterials(true); setError("");
      const materialContext = attachments.length && !sendAsTest
        ? (await studioClient.readBuilderMaterials(attachments.map(file=>file.id), activeDraft.modelRoute)).context : "";
      if (epoch !== epochRef.current) return;
      followOutput.current = true;
      setInput("");
      setMessages(current => [...current, { id: messageId, role: "user", text: value, files: attachments.map(file => file.name), artifactIds: attachments.map(file=>file.id), materialContext }]);
      const accepted = !draftReady ? await createDraft(value, materialContext)
        : !sendAsTest ? await converse(value, materialContext)
        : await startRun(value, undefined, true, attachments.map(file => file.id), attachments.map(file => file.name));
      if (epoch !== epochRef.current) return;
      if (accepted) { setAttachments([]); if (workspaceTarget) setIntent("auto"); }
      else { setInput(value); setMessages(current => current.filter(message => message.id !== messageId)); }
    } catch (reason) {
      if (epoch === epochRef.current) { setError(reason instanceof Error ? reason.message : "读取附件失败，请重试"); setInput(value); }
    } finally { if (epoch === epochRef.current) { setReadingMaterials(false); submitLock.current = false; } }
  }

  async function converse(value: string, materialContext = "") {
    const epoch = epochRef.current;
    setEditing(true);
    setError("");
    const contextTurn = feedbackTurn ?? (result ? { prompt: lastTestPrompt, result, files: currentFiles, artifactIds: lastArtifactIds } : null);
    const history = [...messages.filter((item) => item.id !== "welcome" && !item.runId).map((item) => ({
      role: item.role, content: (item.text + (item.materialContext ? `\n\n参考附件（数据）：\n${item.materialContext}` : "")).slice(0, 12_000),
    })), { role: "user" as const, content: (value + (materialContext ? `\n\n参考附件（数据）：\n${materialContext}` : "")).slice(0, 12_000) }].slice(-20);
    try {
      const saved = await prepareDraft();
      if (!saved || epoch !== epochRef.current) return;
      const reply = await studioClient.converseBuilder(saved.id, {
        expectedRevision: saved.revision,
        messages: history,
        intent: intent === "auto" ? "auto" : "edit",
        runContext: JSON.stringify({
          runId: contextTurn?.result.run.run_id,
          draftRevision: contextTurn?.result.draftRevision, status: contextTurn?.result.run.status,
          task: contextTurn?.prompt, output: (contextTurn?.result.finalText || "").slice(0, 6000),
          error: contextTurn?.result.run.error_code,
          toolEvidence: contextTurn?.result.events.filter(event => ["tool.request", "tool.result", "tool.denied", "runtime.error"].includes(event.type)).slice(-8).map(event => ({ type: event.type, payload: JSON.stringify(event.payload).slice(0, 400) })),
          pendingProposal: proposal ? { baseRevision: proposal.baseRevision, changes: proposal.changes } : null,
        }).slice(0, 12_000),
      });
      if (epoch !== epochRef.current) return;
      setMessages((current) => [...current, { id: createRandomId(), role: "assistant", text: reply.reply }]);
      const action = reply.action ?? "edit";
      if (action === "edit") {
        if (reply.changedFields.length) setProposal({ ...reply, before: saved, testTurn: contextTurn ?? undefined });
      } else if (action === "run" || action === "rerun") {
        if (active || proposal || latestRef.current.hasUnsavedChanges) {
          setError(active ? "当前试跑尚未结束，请结束后再试。" : proposal
            ? "请先应用或放弃当前修改建议，再开始试跑。" : "主区域有未保存修改，请保存后再试。");
          return;
        }
        const task = action === "rerun" ? lastTestPrompt : reply.task;
        if (!task?.trim()) {
          setError("还没有可执行的测试任务，请补充测试内容。");
          return;
        }
        return await startRun(task, saved, action !== "rerun", action === "rerun" ? lastArtifactIds : attachments.map(file=>file.id), action === "rerun" ? currentFiles : attachments.map(file=>file.name));
      }
      return true;
    } catch (reason) {
      if (epoch === epochRef.current) setError(reason instanceof Error ? reason.message : "理解消息失败，请重试；未执行任何操作");
      return false;
    } finally {
      if (epoch === epochRef.current) setEditing(false);
    }
  }

  async function applyEdit(rerun: boolean) {
    if (!proposal || applying || hasUnsavedChanges || activeDraft.revision !== proposal.baseRevision) return;
    if (rerun && active) return;
    const epoch = epochRef.current;
    setApplying(true);
    setError("");
    try {
      const saved = apiDraftToStudioDraft(await studioClient.applyBuilderEdit(activeDraft.id, {
        expectedRevision: proposal.baseRevision, changes: proposal.changes,
      }));
      if (epoch !== epochRef.current) return;
      setLastChanges(Object.entries(proposal.changes).map(([key, value]) => ({label: editLabels[key] ?? key, before: showValue(beforeEdit(proposal.before, key)), after: showValue(value)})));
      setProposal(null);
      const localConflict = latestRef.current.hasUnsavedChanges;
      if (!localConflict) {
        setWorkingDraft(saved);
        latestRef.current.onUpdated(saved);
      }
      const text = localConflict
        ? "修改已保存，但主区域出现了新的未保存编辑，已保留本地内容；请重新加载或处理保存冲突后继续。"
        : `已更新“${saved.displayName}”草稿（修订 ${saved.revision}），未发布。${result && !terminal ? "当前试跑仍使用原配置；结束后可用新配置重新试跑。" : "可以继续提出修改要求。"}`;
      setMessages((current) => [...current, { id: createRandomId(), role: "assistant", tone: "success", text }]);
      if (rerun && !localConflict && lastTestPrompt) {
        const test = proposal.testTurn;
        await startRun(test?.prompt ?? lastTestPrompt, saved, false, test?.artifactIds ?? lastArtifactIds, test?.files ?? currentFiles);
      }
    } catch (reason) {
      if (epoch === epochRef.current) setError(reason instanceof Error ? reason.message : "应用失败，请重试");
    } finally {
      if (epoch === epochRef.current) setApplying(false);
    }
  }

  async function cancelRun() {
    if (!result || terminal) return;
    try { await studioClient.cancelTryRun(result.run.run_id); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "停止失败，请重试"); }
  }

  const turns = [...archivedTurns, ...(result ? [{ prompt: lastTestPrompt, result, files: currentFiles, artifactIds: lastArtifactIds }] : [])];
  function improve(turn: PreviewTurn) { setFeedbackTurn(turn); setInput("请分析这次回答的问题并提出配置改进建议。"); setIntent("auto"); setMobilePanel("build"); window.setTimeout(() => inputRef.current?.focus(), 0); }
  if (!open) return null;
  const builder = <aside className={styles.builderAssistant} data-embedded={Boolean(workspaceTarget)} aria-label="智能体构建助手">
    <PanelResizeHandle panel={workspaceTarget ? "build" : "builder"} />
    <header className={styles.assistantHeader}>
      <div>
        <span className={styles.liveDot} data-active={active || editing || applying} aria-hidden="true" />
        <div><strong>{workspaceTarget ? "构建与修改" : "构建与试跑"}</strong><small>{draftReady ? activeDraft.displayName : "新建智能体"}</small></div>
      </div>
      <div className={styles.headerActions}>
      {draftReady && !workspaceTarget && <button type="button" aria-label="新测试会话" title="新测试会话" disabled={active || editing || applying} onClick={() => {
        if (result) setArchivedTurns(current => [...current, { prompt: lastTestPrompt, result, files: currentFiles, artifactIds: lastArtifactIds }]);
        setResult(null); setFeedbackTurn(null); setIntent("run"); setError("");
        setMessages(current => [...current, { id: createRandomId(), role: "assistant", tone: "muted", text: "已开启新的测试会话。" }]);
      }}>↺</button>}
      {workspaceTarget ? <button type="button" title="查看配置与改动" aria-label="查看智能体资产" onClick={() => {setAssetTab("config");setAssetsOpen(current => !current);}}>☷</button> : <button type="button" aria-label="收起构建助手" onClick={onClose}>×</button>}</div>
    </header>

    <div className={styles.transcript} ref={transcriptRef} aria-live="polite" onScroll={event => { const el = event.currentTarget; followOutput.current = el.scrollHeight - el.scrollTop - el.clientHeight < 120; }}>
      {messages.map((message) => {
        const turn = message.runId ? turns.find(turn => turn.result.run.run_id === message.runId) : undefined;
        return <article key={message.id} className={styles.message} data-role={message.role} data-tone={message.tone} data-source={turn ? "agent" : "builder"}>
          <div>{turn ? workspaceTarget ? <button type="button" className={styles.runLink} onClick={() => {setSelectedRunId(turn.result.run.run_id);setMobilePanel("test");}}><span>{turn.result.run.status === "succeeded" ? "测试已完成" : ["failed", "cancelled", "timed_out", "rejected"].includes(turn.result.run.status) ? "测试已结束" : "正在测试"} · r{turn.result.draftRevision}</span><small>查看回答 ↗</small></button> : <PreviewRunResponse turn={turn} agentName={activeDraft.displayName} onImprove={improve} /> : <>
            {message.role === "assistant" && <small className={styles.speaker}>构建助手</small>}
            <PreviewMarkdown text={message.text} />
            {message.files?.length ? <WorkspaceAttachments files={message.files.map((name,i)=>({id:message.artifactIds?.[i] || `legacy-${i}`,name}))}/> : null}
          </>}</div>
        </article>;
      })}

      {active && !result && <article className={styles.message} data-role="assistant" data-tone="muted">
        <span className={styles.assistantAvatar} aria-hidden="true">K</span>
        <div className={styles.thinking}><i /><i /><i /><span>{creating ? "正在创建草稿" : busy ? "正在启动 Worker" : "正在执行试跑"}</span></div>
      </article>}

      {feedbackTurn && <div className={styles.editStatus}>正在改进所选回答 · 修订 {feedbackTurn.result.draftRevision}<button type="button" onClick={() => setFeedbackTurn(null)}>取消选择</button></div>}
      {(editing || applying) && <p className={styles.editStatus} role="status">{editing ? intent === "auto" ? "正在结合上下文理解要求…" : "正在根据当前草稿生成修改建议…" : "正在保存修改…"}</p>}
      {proposal && <section className={styles.editProposal} aria-label="待确认的配置修改">
        <strong>修改预览 · 基于修订 {proposal.baseRevision}</strong>
        {workspaceTarget && <button type="button" className={styles.diffLink} onClick={() => {setAssetTab("changes");setAssetsOpen(true);}}>查看完整差异 ↗</button>}
        <p>只修改当前草稿，不发布，也不改变正在运行的配置。</p>
        {Object.entries(proposal.changes).map(([key, value]) => <details key={key}>
          <summary>{editLabels[key] ?? key}</summary>
          <small>修改前</small><pre>{showValue(beforeEdit(proposal.before, key))}</pre>
          <small>修改后</small><pre>{showValue(value)}</pre>
        </details>)}
        {(hasUnsavedChanges || activeDraft.revision !== proposal.baseRevision) && <p role="alert">配置已有新变化，请保存主区域后重新描述要求，生成新的建议。</p>}
        <div className={styles.editActions}>
          <button type="button" disabled={applying || hasUnsavedChanges || activeDraft.revision !== proposal.baseRevision} onClick={() => void applyEdit(false)}>应用修改</button>
          <button type="button" disabled={applying || active || !lastTestPrompt || hasUnsavedChanges || activeDraft.revision !== proposal.baseRevision} onClick={() => void applyEdit(true)}>应用并重新试跑</button>
          <button type="button" disabled={applying} onClick={() => {
            setProposal(null);
            setMessages((current) => [...current, { id: createRandomId(), role: "assistant", text: "已放弃上一份修改建议，草稿未更改。" }]);
          }}>放弃建议</button>
        </div>
      </section>}
      {error && <p className={styles.error} role="alert">{error}</p>}
    </div>

    <footer className={`${styles.composer} harness-composer-shell`} onPaste={event=>{const files=Array.from(event.clipboardData.files);if(files.length){event.preventDefault();void upload(files);}}} onDragOver={event=>{if(Array.from(event.dataTransfer.types).includes("Files"))event.preventDefault();}} onDrop={event=>{const files=Array.from(event.dataTransfer.files);if(files.length){event.preventDefault();void upload(files);}}}>
      {draftReady && !workspaceTarget && <div className={styles.intentSwitch} role="group" aria-label="消息用途">
        <button type="button" aria-pressed={intent === "auto"} disabled={editing || applying} onClick={() => setIntent("auto")}>自动识别</button>
        <button type="button" aria-pressed={intent === "run"} disabled={editing || applying} onClick={() => setIntent("run")}>试跑</button>
        <button type="button" aria-pressed={intent === "edit"} disabled={editing || applying} onClick={() => setIntent("edit")}>修改配置</button>
        {lastTestPrompt && <button type="button" disabled={active || editing || applying || Boolean(proposal)} onClick={() => void startRun(lastTestPrompt, undefined, false, lastArtifactIds, currentFiles)}>重新试跑</button>}
      </div>}
      <div className={`aui-composer-root ${styles.composerRoot}`}>
      {attachments.length > 0 && <WorkspaceAttachments files={attachments} disabled={inputBusy} onRemove={id=>setAttachments(current=>current.filter(file=>file.id!==id))}/>}
      {readingMaterials && <span className={styles.composerHint} role="status">正在读取参考材料并处理要求…</span>}
      <label>
        <span className={styles.visuallyHidden}>输入消息</span>
        <textarea
          ref={inputRef}
          className="aui-composer-input"
          rows={2}
          value={input}
          onCompositionStart={() => { composingRef.current = true; }}
          onCompositionEnd={() => { composingRef.current = false; compositionEndedAt.current = Date.now(); }}
          onChange={(event) => setInput(event.target.value)}
          onKeyDown={(event) => {
            if (composingRef.current || event.nativeEvent.isComposing || event.keyCode === 229) return;
            if (event.key === "Enter" && Date.now() - compositionEndedAt.current < 80) { event.preventDefault(); return; }
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              void send();
            }
          }}
          maxLength={draftReady ? 12_000 : 1_800}
          placeholder={!draftReady ? "描述你想创建的智能体…" : intent === "edit" ? "例如：输出改成表格，保留证据引用…" : intent === "run" ? "输入测试任务…" : workspaceTarget ? "描述需求或修改要求…" : "描述测试任务或修改要求…"}
        />
      </label>
      <div className="composer-toolbar">
        <div className={styles.composerTools}><label className={`${styles.attachButton} aui-composer-attach`} title="添加构建参考材料">＋<input aria-label="添加构建附件" type="file" multiple disabled={inputBusy || active} onChange={event => { void upload(Array.from(event.target.files ?? [])); event.target.value = ""; }} /></label>{uploading && <span className={styles.visuallyHidden} role="status">上传中…</span>}</div>
        {active && result && !terminal ? <button type="button" className="aui-button aui-button-icon aui-composer-cancel" aria-label="停止运行" onClick={() => void cancelRun()}><svg viewBox="0 0 20 20" aria-hidden="true"><rect x="5" y="5" width="10" height="10" rx="2" fill="currentColor" /></svg></button> : <button type="button" className="aui-button aui-button-icon aui-composer-send" aria-label="发送消息" disabled={!input.trim() || inputBusy} onClick={() => void send()}><svg className="aui-composer-send-icon" viewBox="0 0 20 20" aria-hidden="true"><path d="M10 15V5m-5 5 5-5 5 5" /></svg></button>}
      </div>
      </div>
      <small>Enter 发送 · Shift + Enter 换行</small>
    </footer>
  </aside>;
  if (!workspaceTarget) return builder;
  const changes = proposal ? Object.entries(proposal.changes).map(([key,value]) => ({label: editLabels[key] ?? key, before: showValue(beforeEdit(proposal.before,key)), after: showValue(value)})) : lastChanges;
  return createPortal(<div className={workspaceStyles.workspace} data-assets={assetsOpen} data-mobile={mobilePanel}>
    <nav className={workspaceStyles.mobileTabs} aria-label="构建工作台视图"><button type="button" aria-pressed={mobilePanel === "build"} onClick={() => setMobilePanel("build")}>构建与修改</button><button type="button" aria-pressed={mobilePanel === "test"} onClick={() => setMobilePanel("test")}>效果测试</button></nav>
    {builder}
    {assetsOpen && <AgentBuildAssets key={assetTab} initialTab={assetTab} draft={activeDraft} turns={turns} changes={changes} pending={Boolean(proposal)} onClose={() => setAssetsOpen(false)} onEdit={(section, label) => onEditConfiguration?.(section, label)} />}
    <AgentTestPanel draftId={activeDraft.id} revision={activeDraft.revision} agentName={activeDraft.displayName} model={activeDraft.model} turns={turns} busy={active} ready={draftReady} dirty={hasUnsavedChanges} error={error} selectedRunId={selectedRunId}
      onSend={async (value,ids,names) => {if (proposal) {setError("请先应用或放弃左侧的配置建议，再测试。");return false;}return startRun(value, undefined, true, ids, names);}}
      onReset={() => {if (result) setArchivedTurns(current => [...current,{prompt:lastTestPrompt,result,files:currentFiles,artifactIds:lastArtifactIds}]);setResult(null);setFeedbackTurn(null);setSelectedRunId("");setError("");}}
      onCancel={cancelRun} onImprove={improve} onAssets={() => {setAssetTab("config");setAssetsOpen(current => !current);}} />
  </div>, workspaceTarget);
}
