"use client";
import { MessageAttachmentView } from "./message-attachment-view";

import Link from "next/link";
import {
  ActionBarPrimitive,
  AttachmentPrimitive,
  BranchPickerPrimitive,
  MessagePrimitive,
  TextMessagePartProvider,
  useAttachment,
  useAui,
  useAuiState,
  useThreadRuntime,
  type CompleteAttachment,
  type ReasoningMessagePartComponent,
  type TextMessagePartProps,
  type ToolCallMessagePartProps,
} from "@assistant-ui/react";
import {
  createContext,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";
import {
  AssistantActionBar,
  AssistantMessage,
  BranchPicker,
  Composer,
  Thread,
  ThreadWelcome,
  UserMessage,
} from "@assistant-ui/react-ui";
import { ConversationIndex } from "./conversation-index";
import { PromptQueue } from "./prompt-queue";
import { useFollowUpPreference } from "../lib/interface-preferences";
import { ConversationInput } from "./conversation-input";
import { ActivitySummary } from "./activity-summary";
import { TaskAgentSwitcher } from "./task-agent-switcher";
import { ApprovalCard, type ApprovalDetails } from "./approval-card";
import { ArtifactCard, type ArtifactDetails } from "./artifact-list";
import { AnswerCitationProvider } from "./knowledge/answer-citation-context";
import { citationsForTurn, parseWikiTarget } from "../lib/knowledge-links";
import { KnowledgeCitations } from "./knowledge/knowledge-citations";
import { WikiPageDrawer } from "./knowledge/wiki-page-drawer";
import { MarkdownText } from "./markdown-text";
import { SubagentCard } from "./subagent-card";
import { ToolCard } from "./tool-card";
import { useRunActivity, useRunViewModel } from "../lib/activity-store";
import {
  reduceRunViewModel,
  selectComposerDisabled,
  type RunCitation,
  type RunPhase,
} from "../lib/run-view-model";
import {
  TaskModelControl,
  TaskModelVisionNotice,
  useTaskModel,
} from "./task-model-context";
import {
  hasRunActivityToolCall,
  runActivitySchema,
  type RunActivity,
} from "../lib/activity-schema";
import { requireAuthenticatedResponse } from "../lib/client-auth";
import {
  approvalStore,
  usePendingApproval,
} from "../lib/approval-store";
import {
  type LiveResponseSnapshot,
  useLiveResponse,
} from "../lib/live-response-store";
import {
  type RunStreamStatus,
  useRunStream,
} from "../lib/run-stream-store";
import { normalizeMessageText } from "../lib/message-text";
import { inputArtifactIdFromAttachment } from "../lib/input-attachment-adapter";
import type { TaskAgent } from "../lib/task-agent-catalog";
import {
  VIDEO_GENERATION_PART_NAME,
  VideoGenerationControls,
  VideoGenerationMessagePart,
  VideoGenerationProvider,
  useVideoGeneration,
} from "./video-generation";

import { createRandomId } from "../lib/random-id";
import { ComposerAssist, composerOptions } from "./composer-assist";
import {
  TaskKnowledgeControl,
  TaskKnowledgeSelection,
  TaskKnowledgeModeSwitch,
  useTaskKnowledge,
} from "./task-knowledge-context";
import { composerTrigger, queueAttachments, queueMayDispatch, restorePromptQueue, type QueuedPrompt } from "../lib/composer-interactions";

export { normalizeMessageText } from "../lib/message-text";
import { runReuseStore, useRunReuseNotice } from "../lib/run-reuse-store";
import {
  loadTaskComposerDraft,
  persistTaskComposerDraft,
} from "../lib/task-composer-draft";
import {
  type UploadFeedback,
  uploadFeedbackStore,
  useUploadFeedback,
} from "../lib/upload-feedback-store";
import {
  skillCreatorPrompt,
  type SkillCreatorLaunch,
} from "../lib/skill-creator-launch";

export function UploadFeedbackContent({
  items,
  onDismiss,
}: {
  items: readonly UploadFeedback[];
  onDismiss: (key: string) => void;
}) {
  if (items.length === 0) return null;
  return (
    <div className="upload-feedback" aria-live="polite" aria-label="附件上传状态">
      {items.map((item) => (
        <div key={item.key} className={`upload-feedback-item ${item.status}`}>
          <span aria-hidden="true">
            {item.status === "uploading" ? "↻" : item.status === "ready" ? "✓" : "!"}
          </span>
          <span>
            <strong>{item.fileName}</strong>
            {item.status === "uploading"
              ? " 正在上传"
              : item.status === "ready"
                ? " 已就绪"
                : ` 上传失败：${item.message ?? "未知错误"}`}
          </span>
          {item.status === "error" ? (
            <button
              type="button"
              onClick={() => onDismiss(item.key)}
              aria-label={`关闭 ${item.fileName} 上传错误`}
            >
              ×
            </button>
          ) : null}
        </div>
      ))}
    </div>
  );
}

function UploadFeedbackNotice() {
  const items = useUploadFeedback();
  return <UploadFeedbackContent items={items} onDismiss={uploadFeedbackStore.dismiss} />;
}

export function shouldShowComposerStop(
  threadRunning: boolean,
  streamStatus: RunStreamStatus,
  runPhase?: RunPhase,
): boolean {
  if (
    runPhase === "completed" ||
    runPhase === "failed" ||
    runPhase === "rejected" ||
    runPhase === "cancelled"
  ) {
    return false;
  }
  return threadRunning || streamStatus === "running" || ["running", "queued", "waiting_approval"].includes(runPhase ?? "");
}

export function shouldShowPreResponseActivity(
  isLastMessage: boolean,
  runPhase?: RunPhase,
): boolean {
  return isLastMessage && (
    runPhase === "queued" ||
    runPhase === "running" ||
    runPhase === "waiting_approval"
  );
}

export async function writeMessageToClipboard(value: string): Promise<boolean> {
  const text = normalizeMessageText(value);
  if (!text) return false;

  try {
    if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    // HTTP deployments and restrictive browser policies can reject the modern
    // Clipboard API. Fall through to the selection-based copy path below.
  }

  if (typeof document === "undefined" || !document.body) return false;
  const activeElement = document.activeElement instanceof HTMLElement
    ? document.activeElement
    : null;
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.inset = "0 auto auto -9999px";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  try {
    return document.execCommand("copy");
  } catch {
    return false;
  } finally {
    textarea.remove();
    activeElement?.focus({ preventScroll: true });
  }
}

function MessageCopyButton({
  text,
  className,
  label,
}: {
  text: string;
  className: string;
  label: string;
}) {
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const resetTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => () => {
    if (resetTimer.current) clearTimeout(resetTimer.current);
  }, []);

  async function copy() {
    const copied = await writeMessageToClipboard(text);
    setCopyState(copied ? "copied" : "failed");
    if (resetTimer.current) clearTimeout(resetTimer.current);
    resetTimer.current = setTimeout(() => setCopyState("idle"), 1800);
  }

  const feedback = copyState === "copied"
    ? "已复制"
    : copyState === "failed"
      ? "复制失败，请手动选择"
      : label;
  return (
    <button
      className={className}
      type="button"
      aria-label={feedback}
      title={feedback}
      data-copy-state={copyState}
      onClick={() => void copy()}
    >
      {copyState === "copied" ? <CopySuccessIcon /> : <CopyMessageIcon />}
      <span className="message-copy-status" aria-live="polite">{feedback}</span>
    </button>
  );
}

type MessageFeedback = "positive" | "negative";

export function feedbackRunId(
  messageId: string,
  isLast: boolean,
  currentRunId?: string,
): string | undefined {
  if (isLast && currentRunId) return currentRunId;
  if (messageId.startsWith("assistant-") && messageId.length > "assistant-".length) {
    return messageId.slice("assistant-".length);
  }
  return undefined;
}

function MessageFeedbackButtons({ runId }: { runId?: string }) {
  const [value, setValue] = useState<MessageFeedback | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!runId || typeof window === "undefined") {
      setValue(null);
      return;
    }
    const saved = window.localStorage.getItem(`harness:run-feedback:${runId}`);
    setValue(saved === "positive" || saved === "negative" ? saved : null);
  }, [runId]);

  async function submit(next: MessageFeedback) {
    if (!runId || pending || value === next) return;
    const previous = value;
    setValue(next);
    setPending(true);
    setError("");
    try {
      const response = requireAuthenticatedResponse(
        await fetch(`/api/studio/runs/${encodeURIComponent(runId)}/feedback`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ value: next === "positive" ? 1 : 0 }),
        }),
      );
      if (!response.ok) {
        throw new Error((await response.text()) || `HTTP ${response.status}`);
      }
      window.localStorage.setItem(`harness:run-feedback:${runId}`, next);
    } catch {
      setValue(previous);
      setError("反馈未提交，请稍后重试");
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <button
        className="assistant-message-feedback"
        data-feedback="positive"
        type="button"
        aria-label="赞同回答"
        title={value ? "反馈已提交" : runId ? "赞同回答" : "本条回答暂无可反馈的运行记录"}
        aria-pressed={value === "positive"}
        disabled={!runId || pending || value !== null}
        onClick={() => void submit("positive")}
      >
        <ThumbUpIcon />
      </button>
      <button
        className="assistant-message-feedback"
        data-feedback="negative"
        type="button"
        aria-label="不赞同回答"
        title={value ? "反馈已提交" : runId ? "不赞同回答" : "本条回答暂无可反馈的运行记录"}
        aria-pressed={value === "negative"}
        disabled={!runId || pending || value !== null}
        onClick={() => void submit("negative")}
      >
        <ThumbDownIcon />
      </button>
      <span className="message-copy-status" aria-live="polite">{error}</span>
    </>
  );
}

function HarnessComposer() {
  const aui = useAui();
  const { routes, overrideRouteId } = useTaskModel();
  const threadRunning = useAuiState((state) => state.thread.isRunning);
  const composerText = useAuiState((state) => state.composer.text);
  const composerAttachments = useAuiState((state) => state.composer.attachments);
  const stream = useRunStream();
  const runView = useRunViewModel();
  const pendingApproval = usePendingApproval();
  const agentSelection = useContext(AgentSelectionContext);
  const skillLaunchContext = useContext(SkillLaunchContext);
  const activeSkillLaunch = skillLaunchContext.launch;
  const selectedSkill = activeSkillLaunch?.name ?? /(?:^|\s)\$([a-zA-Z][\w-]*)(?=\s|$)/u.exec(composerText)?.[1];
  const seededSkillLaunchRef = useRef<string | null>(null);
  const reuseNotice = useRunReuseNotice();
  const runLocked = selectComposerDisabled(runView);
  useTaskComposerDraft(composerText);
  useEffect(() => {
    if (!activeSkillLaunch) {
      seededSkillLaunchRef.current = null;
      return;
    }
    const launchKey = [
      activeSkillLaunch.scope,
      activeSkillLaunch.agentDraftId ?? "",
      activeSkillLaunch.agentLabel ?? "",
    ].join(":");
    if (seededSkillLaunchRef.current === launchKey) return;
    seededSkillLaunchRef.current = launchKey;
    if (composerText.trim()) return;
    aui.composer().setText(skillCreatorPrompt(activeSkillLaunch));
  }, [activeSkillLaunch, aui, composerText]);
  const videoRoute = routes.find(
    (route) => route.id === overrideRouteId && route.modelType === "video_generation",
  );
  const videoGeneration = useVideoGeneration();
  const [videoValidationError, setVideoValidationError] = useState<string | null>(null);
  useEffect(() => {
    const visibleApprovalId = pendingApproval.details?.approval_id;
    if (
      pendingApproval.visible &&
      visibleApprovalId &&
      runView &&
      ["completed", "failed", "rejected", "cancelled"].includes(runView.phase) &&
      runView?.pendingApprovalId !== visibleApprovalId
    ) {
      approvalStore.settle(visibleApprovalId);
    }
  }, [pendingApproval.details?.approval_id, pendingApproval.visible, runView?.pendingApprovalId]);
  const showStop = shouldShowComposerStop(
    threadRunning,
    stream.status,
    runView?.phase,
  );
  const videoGenerating = videoGeneration.generating;
  const threadRuntime = useThreadRuntime();
  const scope = useContext(ComposerDraftContext);
  const queueKey = scope ? `harness:prompt-queue:${scope.userId}:${scope.threadId}` : null;
  const [queue, setQueue] = useState<QueuedPrompt[]>([]);
  const [followUpBehavior] = useFollowUpPreference();
  const [queueLoaded, setQueueLoaded] = useState(false);
  const [queuePaused, setQueuePaused] = useState(false);
  const [inputError, setInputError] = useState("");
  const [helpOpen, setHelpOpen] = useState(false);
  const [caret, setCaret] = useState(0);
  const [suggestionIndex, setSuggestionIndex] = useState(0);
  const [dismissedText, setDismissedText] = useState<string | null>(null);
  const dispatching = useRef(false);
  const [steerAvailable, setSteerAvailable] = useState(false);
  const [steeringIds, setSteeringIds] = useState<string[]>([]);
  const [steeringNotice, setSteeringNotice] = useState("");
  const steeringSentAt = useRef<Record<string, number>>({});
  const steeringRunId = queue.find((item) => item.steerRunId)?.steerRunId ?? runView?.runId;
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const composingRef = useRef(false);
  const knowledge = useTaskKnowledge();
  const [wikiSlug, setWikiSlug] = useState<string | null>(null);
  useEffect(() => {
    const onOpenWiki = (event: Event) => {
      const slug = (event as CustomEvent<{ slug?: string }>).detail?.slug;
      if (slug) setWikiSlug(slug);
    };
    window.addEventListener("harness:open-wiki", onOpenWiki);
    return () => window.removeEventListener("harness:open-wiki", onOpenWiki);
  }, []);
  const options = dismissedText === composerText ? [] : composerOptions(
    composerText,
    caret,
    agentSelection.agents,
    agentSelection.selected?.skills,
    knowledge.available,
    knowledge.selected,
  );
  const busy = threadRunning || showStop || runLocked || videoGenerating;
  useEffect(() => {
    if (!queueKey) return;
    try {
      const saved = restorePromptQueue(localStorage.getItem(queueKey));
      setQueue(saved);
      setSteeringIds(saved.filter((item) => item.steerRunId).map((item) => item.id));
      setQueuePaused(saved.length > 0);
    } catch { setQueue([]); }
    setQueueLoaded(true);
  }, [queueKey]);
  useEffect(() => {
    if (!queueLoaded || !queueKey) return;
    try { localStorage.setItem(queueKey, JSON.stringify(queue)); } catch { /* Keep the in-memory queue. */ }
  }, [queue, queueKey, queueLoaded]);
  useEffect(() => { setSuggestionIndex(0); }, [composerText]);
  const terminalRun = useRef("");
  useEffect(() => {
    if (!runView || !["failed", "rejected", "cancelled"].includes(runView.phase) || terminalRun.current === runView.runId) return;
    terminalRun.current = runView.runId;
    setQueuePaused(true);
  }, [runView?.runId, runView?.phase]);
  useEffect(() => {
    if (busy) { dispatching.current = false; return; }
    if (!queueLoaded || steeringIds.length > 0 || !queue.length || dispatching.current || !queueMayDispatch(busy, queuePaused, runView?.phase)) return;
    // Allow terminal-state effects and durable history synchronization to settle.
    const timer = window.setTimeout(() => {
      if (threadRuntime.getState().isRunning || dispatching.current) return;
      dispatching.current = true;
      const next = queue[0];
      try {
        threadRuntime.append({ role: "user", content: [{ type: "text", text: next.text }], attachments: next.attachments });
        setQueue((current) => current.filter((item) => item.id !== next.id));
      } catch (error) {
        dispatching.current = false;
        setQueuePaused(true);
        setInputError(error instanceof Error ? error.message : "发送失败，队列已暂停。");
      }
    }, 350);
    return () => window.clearTimeout(timer);
  }, [busy, queue, queueLoaded, queuePaused, runView?.phase, steeringIds, threadRuntime]);
  function command(value: string) {
    if (value === "/stop") { void stopRun(); }
    else if (value === "/files") window.dispatchEvent(new Event("harness:open-files"));
    else if (value === "/help") setHelpOpen((current) => !current);
    else if (value === "/new") window.dispatchEvent(new Event("harness:new-task"));
    else if (value !== "/clear") return false;
    aui.composer().setText("");
    return true;
  }
  function chooseSuggestion(index: number) {
    const option = options[index];
    if (!option) return;
    const trigger = composerTrigger(composerText, caret);
    if (!trigger) return;
    if (option.id.startsWith("/")) { command(option.id); return; }
    // `@` selects knowledge bases for this thread; the mention text is removed
    // and the picker stays open so several bases can be toggled in a row.
    if (trigger.symbol === "@") {
      const reference = option.id.slice(1);
      if (busy) return;
      knowledge.toggle(reference);
      const next = composerText.slice(0, trigger.start) + composerText.slice(trigger.end);
      aui.composer().setText(next);
      setDismissedText(null);
      setCaret(trigger.start);
      window.dispatchEvent(new Event("harness:select-knowledge"));
      return;
    }
    const next = composerText.slice(0, trigger.start) + (option.agent ? "" : `${option.id} `) + composerText.slice(trigger.end);
    aui.composer().setText(next);
    setDismissedText(next);
    if (option.agent) agentSelection.onChange(option.agent);
    else inputRef.current?.focus();
  }
  async function enqueue(steer = false) {
    if (!composerText.trim() && !composerAttachments.length) return;
    if (queue.length >= 50) { setInputError("队列已满，请先处理或删除部分消息。"); return; }
    try {
      const next: QueuedPrompt = { id: createRandomId(), text: composerText.trim(), attachments: queueAttachments(composerAttachments) };
      setQueue((current) => [...current, next]);
      setInputError("");
      aui.composer().setText("");
      await threadRuntime.composer.clearAttachments();
      if (steer && steerAvailable && !next.attachments.length) await guide(next);

    } catch (error) { setInputError(error instanceof Error ? error.message : "无法加入队列"); }
  }
  useEffect(() => {
    if (!steeringRunId) { setSteerAvailable(false); return; }
    let active = true;
    const controller = new AbortController();
    async function pollSteering() {
      try {
        const response = requireAuthenticatedResponse(await fetch(`/api/harness/runs/${encodeURIComponent(steeringRunId!)}/steer`, { cache: "no-store", signal: controller.signal }));
        if (!response.ok) { if (active) setSteerAvailable(false); return; }
        const state = await response.json() as { available: boolean; requests: Array<{ request_id: string; status: string; error?: string }> };
        if (!active) return;
        setSteerAvailable(state.available && steeringRunId === runView?.runId);
        for (const id of steeringIds) {
          if (state.requests.some((item) => item.request_id === id)) continue;
          steeringSentAt.current[id] ??= Date.now();
          if (Date.now() - steeringSentAt.current[id] > 10_000) {
            setSteeringIds((ids) => ids.filter((value) => value !== id));
            setQueue((items) => items.map((entry) => entry.id === id ? { ...entry, steerRunId: undefined } : entry));
            setQueuePaused(true);
            setInputError("未查到引导接收记录，内容已保留，可再次点击引导核对。");
          }
        }
        for (const item of state.requests) {
          if (!steeringIds.includes(item.request_id)) continue;
          if (item.status === "accepted") {
            setQueue((items) => items.filter((entry) => entry.id !== item.request_id));
            setSteeringIds((ids) => ids.filter((id) => id !== item.request_id));
            setSteeringNotice("已送入当前运行，智能体会在后续处理时参考补充。");
          } else if (["failed", "not_delivered", "unknown"].includes(item.status)) {
            setSteeringIds((ids) => ids.filter((id) => id !== item.request_id));
            setQueuePaused(true);
            setQueue((items) => items.map((entry) => entry.id === item.request_id ? { ...entry, steerRunId: undefined } : entry));
            setInputError(item.status === "unknown" ? "引导接收状态不确定，请先查看回复再决定是否重发。" : item.error || "运行已经结束，补充仍保留在队列中。");
          }
        }
      } catch { if (active) setSteerAvailable(false); }
    }
    void pollSteering();
    const timer = (busy && !steerAvailable) || steeringIds.length > 0
      ? window.setInterval(() => void pollSteering(), 1500) : undefined;
    return () => { active = false; controller.abort(); window.clearInterval(timer); };
  }, [runView?.runId, runView?.phase, steeringIds, busy, steerAvailable, steeringRunId]);
  async function guide(item: QueuedPrompt) {
    if (!runView?.runId || !steerAvailable || steeringIds.includes(item.id)) return;
    if (item.attachments.length) { setInputError("带附件的补充请加入队列；实时引导目前支持文本。"); return; }
    setQueuePaused(true);
    setSteeringIds((ids) => [...ids, item.id]);
    steeringSentAt.current[item.id] = Date.now();
    setQueue((items) => items.map((entry) => entry.id === item.id ? { ...entry, steerRunId: runView.runId } : entry));
    setInputError("");
    setSteeringNotice("");
    try {
      const response = requireAuthenticatedResponse(await fetch(`/api/harness/runs/${encodeURIComponent(runView.runId)}/steer`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ request_id: item.id, text: item.text }),
      }));
      if (!response.ok) {
        setSteeringIds((ids) => ids.filter((id) => id !== item.id));
        setQueue((items) => items.map((entry) => entry.id === item.id ? { ...entry, steerRunId: undefined } : entry));
        setInputError("当前运行无法接收引导，内容仍保留在队列中。");
      }
    } catch { setInputError("引导请求状态待确认，正在核对接收结果；请勿重复发送。"); }
  }
  useEffect(() => {
    if (queueLoaded && queue.length === 0 && steeringIds.length === 0) setQueuePaused(false);
  }, [queueLoaded, queue.length, steeringIds.length]);
  async function stopRun() {
    setQueuePaused(true);
    if (threadRunning) { aui.thread().cancelRun(); return; }
    if (!runView?.runId || !showStop) return;
    try {
      const response = requireAuthenticatedResponse(await fetch(`/api/agui/runs/${encodeURIComponent(runView.runId)}/cancel`, { method: "POST" }));
      if (!response.ok) throw new Error("停止请求未成功，请重试。");
      setSteeringNotice("已请求停止，待发送内容已暂停。");
    } catch (error) { setInputError(error instanceof Error ? error.message : "停止请求未成功，请重试。"); }
  }
  function submitComposer(alternate = false) {
    if (command(composerText.trim())) return;
    if (videoRoute) { void generateVideo(); return; }
    if (busy || queue.length) {
      const steer = alternate ? followUpBehavior !== "steer" : followUpBehavior === "steer";
      void enqueue(busy && steer); return;
    }
    aui.composer().send();
  }

  async function generateVideo() {
    const prompt = composerText.trim();
    if (!videoRoute || !prompt || videoGenerating) return;
    setVideoValidationError(null);
    if (composerAttachments.some((attachment) => attachment.type !== "image")) {
      setVideoValidationError("H3 参考素材只支持图片，请移除文档或其他文件。");
      return;
    }
    if (videoGeneration.settings.mode === "ref2va" && composerAttachments.length === 0) {
      setVideoValidationError("Ref2VA 至少需要添加一张参考图片。");
      return;
    }
    const maximumReferences = videoGeneration.settings.mode === "ref2va" ? 9 : 2;
    if (composerAttachments.length > maximumReferences) {
      setVideoValidationError(
        videoGeneration.settings.mode === "ref2va"
          ? "Ref2VA 最多使用九张参考图片。"
          : "自动模式最多使用两张参考图片。",
      );
      return;
    }
    const maybeArtifactIds = composerAttachments.map(inputArtifactIdFromAttachment);
    if (maybeArtifactIds.some((item) => !item)) {
      setVideoValidationError("参考图片仍在上传，请稍后再试。");
      return;
    }
    const inputArtifactIds = maybeArtifactIds.filter(
      (item): item is string => Boolean(item),
    );
    const seed = videoGeneration.settings.seed.trim();
    if (seed && (!/^\d+$/.test(seed) || !Number.isSafeInteger(Number(seed)))) {
      setVideoValidationError("随机种子必须是非负整数。");
      return;
    }
    const attachments: CompleteAttachment[] = composerAttachments.map((attachment, index) => {
      const artifactId = inputArtifactIds[index]!;
      const mimeType = attachment.contentType ?? "image/*";
      return {
        id: artifactId,
        type: "image",
        name: attachment.name,
        contentType: mimeType,
        status: { type: "complete" },
        content: [{
          type: "file",
          data: artifactId,
          mimeType,
          filename: attachment.name,
        }],
      };
    });
    videoGeneration.start({
      routeId: videoRoute.id,
      routeLabel: videoRoute.label,
      prompt,
      inputArtifactIds,
      attachments,
    });
  }
  return (
    <div
      className="harness-composer-shell"
      data-run-phase={runView?.phase ?? "idle"}
      data-run-locked={runLocked ? "true" : "false"}
      aria-busy={runLocked}
    >
      {reuseNotice ? (
        <div className="composer-run-reuse-notice" role="status">
          <span>
            已返回正在执行的原任务
            <small>{reuseNotice.runId}</small>
          </span>
          <button type="button" onClick={runReuseStore.clear} aria-label="关闭提示">
            ×
          </button>
        </div>
      ) : null}
      {runView?.phase === "queued" && runView.queueReason ? (
        <div className="composer-queue-notice" role="status">
          <span>
            <strong>{runView.queueReason}</strong>
            {runView.blockedByRunId ? ` · ${runView.blockedByRunId}` : ""}
          </span>
          {pendingApproval.visible && pendingApproval.details ? (
            <button
              type="button"
              onClick={() =>
                document
                  .querySelector<HTMLElement>(".composer-approval-slot")
                  ?.scrollIntoView({ behavior: "smooth", block: "center" })
              }
            >
              直达审批
            </button>
          ) : null}
        </div>
      ) : null}
      {pendingApproval.visible && pendingApproval.details ? (
        <div className="composer-approval-slot">
          <ApprovalCard
            details={pendingApproval.details}
            complete={false}
            onDecision={async (decision) => {
              const approvalId = pendingApproval.details!.approval_id;
              const response = requireAuthenticatedResponse(
                await fetch(
                  `/api/harness/approvals/${encodeURIComponent(approvalId)}`,
                  {
                    method: "PUT",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ decision }),
                  },
                ),
              );
              if (!response.ok) throw new Error(await response.text());
              approvalStore.settle(approvalId);
            }}
          />
        </div>
      ) : null}
      <UploadFeedbackNotice />
      <TaskModelVisionNotice
        disabled={runLocked || showStop || videoGenerating}
        requiresVision={composerAttachments.some((attachment) => attachment.type === "image")}
      />
      {videoRoute ? (
        <>
          <VideoGenerationControls
            label={videoRoute.label}
            referenceCount={composerAttachments.length}
            disabled={videoGenerating}
          />
          {videoValidationError ? (
            <p className="composer-video-validation" role="alert">
              {videoValidationError}
            </p>
          ) : null}
        </>
      ) : null}
      <PromptQueue items={queue} paused={queuePaused} busy={busy} canSteer={steerAvailable} sendingIds={steeringIds}
        onChange={setQueue} onPause={(value) => { dispatching.current = false; setQueuePaused(value); }} onGuide={(item) => void guide(item)}
        onSend={(item) => {
          if (busy || threadRuntime.getState().isRunning || steeringIds.length) return;
          setQueuePaused(true);
          try {
            threadRuntime.append({ role: "user", content: [{ type: "text", text: item.text }], attachments: item.attachments });
            setQueue((items) => items.filter((entry) => entry.id !== item.id));
          } catch { setInputError("发送失败，消息仍保留在队列中。"); }
        }} />
      {steeringNotice && <p className="composer-status-announcement" role="status">{steeringNotice}</p>}
      {inputError && <p className="composer-input-error" role="alert">{inputError}</p>}
      <TaskKnowledgeSelection disabled={busy} />
      <Composer.Root onSubmitCapture={(event: FormEvent) => { event.preventDefault(); event.stopPropagation(); if (!composingRef.current) submitComposer(); }}>
        <ComposerAssist options={options} index={suggestionIndex} onChoose={chooseSuggestion} />
        {wikiSlug ? (
          <WikiPageDrawer
            key={wikiSlug}
            reference={parseWikiTarget(wikiSlug).reference}
            slug={parseWikiTarget(wikiSlug).slug}
            onClose={() => setWikiSlug(null)}
          />
        ) : null}
        {helpOpen && <div className="composer-assist composer-help-popover" role="dialog" aria-label="输入帮助">
          <button type="button" onClick={() => setHelpOpen(false)}>关闭</button>
          <p>/ 执行命令 · @ 选择知识库（可多选） · $ 引用技能</p>
          <p>运行中 Enter {followUpBehavior === "steer" ? "调整方向" : "加入队列"}，Alt Enter 使用另一种方式，Shift Enter 换行。可在个人设置的配置中更改默认行为。</p>
        </div>}
        {activeSkillLaunch && selectedSkill && (
          <div className="composer-skill-context" aria-label="已识别的 Skill 提示" title="直接输入 $技能名 即可，无需额外点选；也可以直接描述需求。">
            <span aria-hidden="true">$</span><strong>{selectedSkill}</strong>
            <button type="button" aria-label={`移除技能 ${selectedSkill}`} onClick={() => {
              aui.composer().setText(composerText.replace(new RegExp(`\\$${selectedSkill}(?=\\s|$)`, "u"), "").trimStart());
              if (activeSkillLaunch) skillLaunchContext.onDismiss();
            }}>×</button>
          </div>
        )}
        <Composer.Attachments />
        <ConversationInput
          ref={inputRef}
          onComposingChange={(value) => { composingRef.current = value; }}
          className="aui-composer-input"
          aria-label="消息输入"
          placeholder={busy ? "继续补充…" : knowledge.selected.length ? "输入问题，将基于上方选中的知识库回答" : "随心输入，/ 命令 · @ 知识库 · $ 技能"}
          rows={Math.min(8, Math.max(2, composerText.split("\n").length))}
          aria-controls={options.length ? "composer-suggestions" : undefined}
          aria-activedescendant={options.length ? `composer-option-${suggestionIndex}` : undefined}
          onChange={(event) => { if (!composingRef.current) setCaret(event.target.selectionStart); }}
          onCompositionEnd={(event) => setCaret(event.currentTarget.selectionStart)}
          onSelect={(event) => setCaret(event.currentTarget.selectionStart)}
          onPaste={(event) => {
            const files = Array.from(event.clipboardData.files);
            if (!files.length) return;
            event.preventDefault();
            for (const file of files) void threadRuntime.composer.addAttachment(file).catch(() => setInputError("附件添加失败，请重试。"));
          }}
          onKeyDown={(event: ReactKeyboardEvent<HTMLTextAreaElement>) => {
            if (event.nativeEvent.isComposing || composingRef.current) return;
            if (options.length && ["ArrowDown", "ArrowUp", "Escape", "Enter", "Tab"].includes(event.key) && !event.shiftKey) {
              event.preventDefault();
              if (event.key === "Escape") setDismissedText(composerText);
              else if (event.key === "ArrowDown") setSuggestionIndex((value) => (value + 1) % options.length);
              else if (event.key === "ArrowUp") setSuggestionIndex((value) => (value + options.length - 1) % options.length);
              else chooseSuggestion(suggestionIndex);
            } else if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submitComposer(event.altKey);
            }
          }}
        />
        <div className="composer-footer">
        <div className="composer-toolbar">
          <Composer.AddAttachment>
            <svg className="aui-composer-attach-icon" viewBox="0 0 20 20" aria-hidden="true">
              <path d="M10 4.5v11M4.5 10h11" />
            </svg>
          </Composer.AddAttachment>
          <TaskKnowledgeControl disabled={runLocked || showStop || videoGenerating} />
          <TaskAgentSwitcher
            agents={agentSelection.agents}
            selected={agentSelection.selected}
            loading={agentSelection.loading}
            currentTaskBusy={agentSelection.currentTaskBusy}
            onChange={agentSelection.onChange}
            onRefresh={agentSelection.onRefresh}
          />
          <TaskKnowledgeModeSwitch disabled={runLocked || showStop || videoGenerating} />
          <TaskModelControl disabled={runLocked || showStop || videoGenerating} />
          {showStop && Boolean(composerText.trim() || composerAttachments.length) && <button type="button" className="composer-stop-secondary" aria-label="停止运行" title="停止运行" onClick={() => void stopRun()}><svg viewBox="0 0 20 20" aria-hidden="true"><rect x="5" y="5" width="10" height="10" rx="2" fill="currentColor" /></svg></button>}
        </div>
        {showStop && !composerText.trim() && !composerAttachments.length ? (
          <button
            type="button"
            className="aui-button aui-button-icon aui-composer-cancel"
            aria-label="停止运行"
            onClick={() => void stopRun()}
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <rect x="6" y="6" width="12" height="12" rx="2.5" fill="currentColor" />
            </svg>
          </button>
        ) : videoRoute ? (
          <button
            type="button"
            className="aui-button aui-composer-video-send"
            disabled={videoGenerating || !composerText.trim()}
            aria-label={videoGenerating ? "视频生成中" : "生成视频"}
            onClick={() => void generateVideo()}
          >
            {videoGenerating ? "生成中" : "生成视频"}
          </button>
        ) : (
          <button type="button" className="aui-button aui-button-icon aui-composer-send" aria-label={busy ? followUpBehavior === "steer" && steerAvailable && !composerAttachments.length ? "调整方向" : "加入队列" : queue.length ? "加入队列" : "发送消息"} title={busy ? `Enter ${followUpBehavior === "steer" ? "调整方向" : "加入队列"} · Alt Enter 切换` : "发送消息"} disabled={!composerText.trim() && !composerAttachments.length} onClick={() => submitComposer()}>
            <svg className="aui-composer-send-icon" viewBox="0 0 20 20" aria-hidden="true">
              <path d="M10 16.5v-11M5.5 9.5 10 5l4.5 4.5" />
            </svg>
          </button>
        )}
        </div>
      </Composer.Root>

    </div>
  );
}

type ComposerDraftScope = {
  userId: string;
  threadId: string;
};

const ComposerDraftContext = createContext<ComposerDraftScope | null>(null);
type SkillLaunchContextValue = {
  launch: SkillCreatorLaunch | null;
  onDismiss: () => void;
};

const SkillLaunchContext = createContext<SkillLaunchContextValue>({
  launch: null,
  onDismiss: () => undefined,
});

type AgentSelectionContextValue = {
  agents: readonly TaskAgent[];
  selected: TaskAgent | null;
  loading: boolean;
  currentTaskBusy: boolean;
  onChange: (agent: TaskAgent) => void;
  onRefresh?: () => void;
};

const AgentSelectionContext = createContext<AgentSelectionContextValue>({
  agents: [],
  selected: null,
  loading: true,
  currentTaskBusy: false,
  onChange: () => undefined,
});

function useTaskComposerDraft(text: string) {
  const scope = useContext(ComposerDraftContext);
  const aui = useAui();
  const auiRef = useRef(aui);
  const latestText = useRef(text);
  const [restored, setRestored] = useState(false);
  auiRef.current = aui;
  latestText.current = text;

  useEffect(() => {
    if (!scope) return;
    setRestored(false);
    const saved = loadTaskComposerDraft(
      window.localStorage,
      scope.userId,
      scope.threadId,
    );
    if (saved && !latestText.current) {
      auiRef.current.composer().setText(saved);
    }
    setRestored(true);
  }, [scope]);

  useEffect(() => {
    if (!scope || !restored) return;
    const timer = window.setTimeout(() => {
      persistTaskComposerDraft(
        window.localStorage,
        scope.userId,
        scope.threadId,
        text,
      );
    }, 220);
    return () => window.clearTimeout(timer);
  }, [restored, scope, text]);

  useEffect(() => {
    if (!scope) return;
    return () => {
      persistTaskComposerDraft(
        window.localStorage,
        scope.userId,
        scope.threadId,
        latestText.current,
      );
    };
  }, [scope]);

  return restored;
}

function ApprovalToolBridge({
  details,
  complete,
}: {
  details: ApprovalDetails;
  complete: boolean;
}) {
  useEffect(() => {
    if (complete) approvalStore.settle(details.approval_id);
    else approvalStore.show(details);
  }, [complete, details]);
  return null;
}

export function UserTaskWelcome() {
  return (
    <ThreadWelcome.Root className="user-task-welcome">
      <ThreadWelcome.Center className="user-task-hero">
        <div className="user-task-intro">
          <p className="user-task-kicker"><span aria-hidden="true" />Agent Studio</p>
          <h1>开始一个新任务</h1>
          <p>
            描述要达成的结果，或附上资料。执行过程、工具调用和产出，都会留在这段对话里。
          </p>
        </div>
      </ThreadWelcome.Center>
    </ThreadWelcome.Root>
  );
}

function toolStatus(part: ToolCallMessagePartProps) {
  if (part.result !== undefined) return "complete" as const;
  if (part.argsText) return "executing" as const;
  return "inProgress" as const;
}

function objectValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? { ...(value as Record<string, unknown>) }
    : {};
}

function useAssistantResponseStarted() {
  return useAuiState((state) =>
    state.message.content.some(
      (part) => part.type === "text" && part.text.trim().length > 0,
    ),
  );
}

export function hasProjectedTool(
  view: ReturnType<typeof useRunViewModel>,
  toolCallId: string | undefined,
) {
  return Boolean(
    toolCallId && view?.tools.some((tool) => tool.id === toolCallId),
  );
}

export function shouldSuppressRawToolCard(
  view: ReturnType<typeof useRunViewModel>,
  toolCallId: string | undefined,
) {
  // A Harness run activity is the canonical, durable projection of ordinary
  // tools. SDK/assistant-ui tool parts can be incomplete after a failed or
  // resumed run and otherwise fall back to a permanently-open raw JSON card.
  return Boolean(view) || hasProjectedTool(view, toolCallId);
}

export function shouldKeepActivityInLatestSlot(
  activityRunId: string,
  viewRunId: string | undefined,
) {
  return viewRunId === activityRunId;
}

export function shouldShowArtifactForTurn(
  artifactRunId: string | undefined,
  viewRunId: string | undefined,
  isLast: boolean,
) {
  return !(
    isLast &&
    artifactRunId &&
    viewRunId &&
    artifactRunId !== viewRunId
  );
}

function HarnessToolPart(part: ToolCallMessagePartProps) {
  const status = toolStatus(part);
  const args = objectValue(part.args);
  const runView = useRunViewModel();
  if (part.toolName === "harness_run_activity") {
    const parsed = runActivitySchema.safeParse(args.activity);
    if (
      !parsed.success ||
      shouldKeepActivityInLatestSlot(
        parsed.data.run_id,
        runView?.runId,
      )
    ) {
      return null;
    }
    return (
      <div className="turn-activity-summary">
        <ActivitySummary activity={parsed.data} responseStarted />
      </div>
    );
  }
  if (part.toolName === "Task" || part.toolName === "Agent") {
    return <SubagentCard status={status} parameters={args} result={part.result} />;
  }
  if (part.toolName === "harness_request_approval") {
    const details = args as unknown as ApprovalDetails;
    return (
      <ApprovalToolBridge
        details={details}
        complete={part.result !== undefined}
      />
    );
  }
  if (part.toolName === "harness_present_artifact") {
    return <HarnessArtifactPart args={args} runId={runView?.runId} />;
  }
  if (shouldSuppressRawToolCard(runView, part.toolCallId)) {
    return null;
  }
  return (
    <ToolCard
      toolCallId={part.toolCallId}
      name={part.toolName}
      status={status}
      args={args}
      result={part.result}
      isError={part.isError}
    />
  );
}

function HarnessArtifactPart({
  args,
  runId,
}: {
  args: Record<string, unknown>;
  runId: string | undefined;
}) {
  const isLast = useAuiState((state) => state.message.isLast);
  const artifactRunId =
    typeof args.run_id === "string" ? args.run_id : undefined;
  if (!shouldShowArtifactForTurn(artifactRunId, runId, isLast)) return null;
  return <ArtifactCard details={args as unknown as ArtifactDetails} />;
}

const ReasoningPart: ReasoningMessagePartComponent = ({ text, status }) => (
  <details className="reasoning-card" data-active={status.type === "running"} open={status.type === "running"}>
    <summary>
      <span className="reasoning-mark" aria-hidden="true" />
      <span>{status.type === "running" ? "正在思考" : "已思考"}</span>
      <small>{status.type === "running" ? "进行中" : "展开查看"}</small>
    </summary>
    <div>{text}</div>
  </details>
);

type AssistantPartLike = {
  type?: string;
  toolName?: string;
};

const responseProjectionToolNames = new Set([
  "harness_run_activity",
  "harness_present_artifact",
]);

function isOperationalToolPart(part: AssistantPartLike) {
  return (
    part.type === "tool-call" &&
    !responseProjectionToolNames.has(part.toolName ?? "")
  );
}

export function isIntermediateAssistantTextPart(
  parts: readonly AssistantPartLike[],
  partIndex: number,
) {
  return (
    partIndex >= 0 &&
    parts[partIndex]?.type === "text" &&
    parts.slice(partIndex + 1).some(isOperationalToolPart)
  );
}

function HarnessAssistantText(part: TextMessagePartProps) {
  const aui = useAui();
  const live = useLiveResponse();
  const isLast = useAuiState((state) => state.message.isLast);
  const messageId = useAuiState((state) => state.message.id);
  const parts = useAuiState((state) => state.message.content);
  const partIndex =
    aui.part.source === "message" && aui.part.query.type === "index"
      ? aui.part.query.index
      : -1;
  if (
    shouldSuppressNativeAssistantText(
      ownsLiveResponse(isLast, messageId, live.messageId),
      live.status,
    ) || isIntermediateAssistantTextPart(parts, partIndex)
  ) {
    return null;
  }
  return (
    <div
      className="assistant-answer"
      data-streaming={part.status.type === "running" ? "true" : "false"}
      aria-busy={part.status.type === "running"}
    >
      <MarkdownText />
    </div>
  );
}

function LiveAssistantResponse({
  live,
  ownsMessage,
}: {
  live: LiveResponseSnapshot;
  ownsMessage: boolean;
}) {
  if (!ownsMessage || !live.visible || !live.text.trim()) return null;
  const streaming = live.status === "streaming";
  return (
    <div
      className="assistant-answer live-assistant-response"
      data-streaming={streaming ? "true" : "false"}
      aria-busy={streaming}
      aria-live="polite"
    >
      <TextMessagePartProvider text={live.text} isRunning={streaming}>
        <MarkdownText />
      </TextMessagePartProvider>
    </div>
  );
}

function TurnActivity({
  hasDurableProjection,
  messageId,
}: {
  hasDurableProjection: boolean;
  messageId: string;
}) {
  const activity = useRunActivity();
  const runView = useRunViewModel();
  const isLast = useAuiState((state) => state.message.isLast);
  const ownedActivity = activity && turnOwnsRun(
    messageId,
    activity.run_id,
    isLast,
    runView?.runId,
  )
    ? activity
    : undefined;
  const responseStarted = useAssistantResponseStarted();
  const [capturedActivity, setCapturedActivity] = useState(ownedActivity);

  useEffect(() => {
    if (
      ownedActivity &&
      shouldCaptureTurnActivity(
        ownedActivity.run_id,
        capturedActivity?.run_id,
        isLast,
        runView?.runId,
      )
    ) {
      setCapturedActivity(ownedActivity);
    }
  }, [ownedActivity, capturedActivity?.run_id, isLast, runView?.runId]);

  // Reloaded history already contains a per-turn tool projection. Live
  // assistant-ui messages do not, so retain the last snapshot on the turn
  // when a newer user message makes it stop being the latest message.
  const displayed = selectTurnActivity(
    ownedActivity,
    capturedActivity,
    isLast,
    hasDurableProjection,
  );
  if (
    !displayed ||
    !turnOwnsRun(messageId, displayed.run_id, isLast, runView?.runId)
  ) return null;

  return (
    <div
      className={`latest-activity ${displayed.status}`}
      data-activity-source={isLast ? "current-run" : "captured-turn"}
    >
      <ActivitySummary
        activity={displayed}
        responseStarted={!isLast || responseStarted}
      />
    </div>
  );
}

export function selectTurnActivity(
  current: RunActivity | undefined,
  captured: RunActivity | undefined,
  isLast: boolean,
  hasDurableProjection: boolean,
) {
  if (isLast && current) return current;
  if (hasDurableProjection) return undefined;
  return captured;
}

export function shouldCaptureTurnActivity(
  activityRunId: string,
  capturedRunId: string | undefined,
  isLast: boolean,
  viewRunId: string | undefined,
) {
  if (viewRunId !== activityRunId) return false;
  // The terminal activity delta and AG-UI RUN_FINISHED arrive back-to-back.
  // React may batch them so the message is no longer "last" before this effect
  // observes the terminal delta. Keep accepting updates for the Run already
  // captured by this turn, but never adopt a newer Run into an older turn.
  return isLast || capturedRunId === activityRunId;
}

export function incompleteRunGuidance(isLast: boolean) {
  return isLast
    ? "本次运行未完整结束，可打开“运行详情”查看原因。"
    : "该条历史运行未完整结束，可打开“运行详情”查看原因。";
}

export function shouldOfferIncompleteRetry(
  status: { type?: string; reason?: string } | undefined,
) {
  return status?.type === "incomplete" && status.reason !== "cancelled";
}

export function ownsLiveResponse(
  isLast: boolean,
  messageId: string,
  liveMessageId: string | undefined,
) {
  return isLast && Boolean(liveMessageId) && messageId === liveMessageId;
}

export function shouldSuppressNativeAssistantText(
  ownsLive: boolean,
  liveStatus: LiveResponseSnapshot["status"],
) {
  return ownsLive && liveStatus !== "idle";
}

export function messageOwnsRun(messageId: string, runId: string) {
  const prefix = `assistant-${runId}`;
  return messageId === prefix || messageId.startsWith(`${prefix}-`);
}

export function turnOwnsRun(
  messageId: string,
  activityRunId: string,
  isLast: boolean,
  viewRunId: string | undefined,
) {
  // History recovery creates an optimistic assistant message whose random ID
  // cannot contain the durable server run ID.  The current Activity snapshot
  // is still authoritative for the latest turn, so keep it attached there.
  return messageOwnsRun(messageId, activityRunId) || (
    isLast && viewRunId === activityRunId
  );
}

function HarnessAssistantMessage() {
  const live = useLiveResponse();
  const isLast = useAuiState((state) => state.message.isLast);
  const messageId = useAuiState((state) => state.message.id);
  const messageStatus = useAuiState((state) => state.message.status);
  const runView = useRunViewModel();
  const showIncompleteRecovery = shouldOfferIncompleteRetry(messageStatus);
  const content = useAuiState((state) => state.message.content);
  const hasVideoGeneration = content.some(
    (part) => part.type === "data" && part.name === VIDEO_GENERATION_PART_NAME,
  );
  // Own the native text slot as soon as a Harness message starts. Candidate
  // text may still be waiting to see whether a tool call follows, so basing
  // this only on visible text lets assistant-ui paint the same preface once.
  const ownsLive = ownsLiveResponse(isLast, messageId, live.messageId);
  const directStream = ownsLive && live.status !== "idle";
  const copyText = ownsLive && live.text.trim()
    ? normalizeMessageText(live.text)
    : normalizeMessageText(
        content
          .flatMap((part, index) => (
            part.type === "text" && !isIntermediateAssistantTextPart(content, index)
              ? [part.text]
              : []
          ))
          .join("\n"),
      );
  const feedbackRun = feedbackRunId(messageId, isLast, runView?.runId);
  // Requirement: citations must be reachable from the reply itself, not only
  // from the collapsed execution summary. Deduplicate by chunk so repeated
  // retrieval in one run shows one chip per slice.
  const durablePart = content.find((part) => part.type === "tool-call" && part.toolName === "harness_run_activity");
  const durable = durablePart?.type === "tool-call" ? runActivitySchema.safeParse(durablePart.args.activity) : null;
  const capturedCitations = useRef<{ messageId: string; citations: RunCitation[] }>({ messageId, citations: [] });
  const turnCitations = citationsForTurn(messageId, isLast, runView, durable?.success ? durable.data : undefined);
  if (turnCitations) capturedCitations.current = { messageId, citations: turnCitations };
  const answerCitations = capturedCitations.current.messageId === messageId ? capturedCitations.current.citations : [];
  return (
    <AnswerCitationProvider citations={answerCitations}>
    <AssistantMessage.Root
      className="harness-assistant-message"
      data-turn-answer={copyText.replace(/\s+/g, " ").slice(0, 360)}
      data-direct-stream={directStream ? "true" : "false"}
    >
      <TurnActivity
        hasDurableProjection={hasRunActivityToolCall(content)}
        messageId={messageId}
      />
      <LiveAssistantResponse live={live} ownsMessage={ownsLive} />
      <AssistantMessage.Content
        components={{
          Text: HarnessAssistantText,
          Reasoning: ReasoningPart,
          data: {
            by_name: {
              [VIDEO_GENERATION_PART_NAME]: VideoGenerationMessagePart,
            },
          },
        }}
      />
      {answerCitations.length > 0 ? (
        <KnowledgeCitations citations={answerCitations} showSources={false} />
      ) : null}
      {showIncompleteRecovery ? (
        <div className="aui-message-error">
          <span>{incompleteRunGuidance(isLast)}</span>
          <ActionBarPrimitive.Reload
            className="run-retry-button"
            aria-label="重新运行"
            title="重新运行"
          >
            重新运行
          </ActionBarPrimitive.Reload>
        </div>
      ) : null}
      {!hasVideoGeneration ? (
        <div className="assistant-message-controls">
          <HarnessBranchPicker />
          <AssistantActionBar.Root
            className="assistant-feedback-actions"
            hideWhenRunning
            autohide="not-last"
            autohideFloat="single-branch"
          >
            <MessageCopyButton
              className="assistant-message-copy"
              label="复制回答"
              text={copyText}
            />
            <MessageFeedbackButtons runId={feedbackRun} />
            <TurnCompletion />
          </AssistantActionBar.Root>
        </div>
      ) : null}
    </AssistantMessage.Root>
    </AnswerCitationProvider>
  );
}

function TurnCompletion() {
  const content = useAuiState((state) => state.message.content);
  const isLast = useAuiState((state) => state.message.isLast);
  const observed = useRunViewModel();
  const durable = content.find((part) => part.type === "tool-call" && part.toolName === "harness_run_activity");
  const parsed = durable?.type === "tool-call" ? runActivitySchema.safeParse(durable.args.activity) : null;
  const activity = parsed?.success ? parsed.data : null;
  const view = isLast && observed ? observed : activity ? reduceRunViewModel(undefined, activity) : null;
  if (!view || view.phase !== "completed") return null;
  const date = new Date(view.updatedAt);
  if (!Number.isFinite(date.getTime())) return null;
  return <time className="turn-completion" dateTime={view.updatedAt} title={date.toLocaleString("zh-CN")}>
    {date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false })}
  </time>;
}

function HarnessBranchPicker() {
  return (
    <BranchPickerPrimitive.Root
      className="harness-branch-picker"
      hideWhenSingleBranch
    >
      <BranchPickerPrimitive.Previous asChild>
        <button type="button" aria-label="上一个回答" title="上一个回答">
          <svg viewBox="0 0 20 20" aria-hidden="true">
            <path d="m12.5 4.5-5 5.5 5 5.5" />
          </svg>
        </button>
      </BranchPickerPrimitive.Previous>
      <span className="harness-branch-state" aria-label="回答版本">
        <BranchPickerPrimitive.Number />
        <i aria-hidden="true">/</i>
        <BranchPickerPrimitive.Count />
      </span>
      <BranchPickerPrimitive.Next asChild>
        <button type="button" aria-label="下一个回答" title="下一个回答">
          <svg viewBox="0 0 20 20" aria-hidden="true">
            <path d="m7.5 4.5 5 5.5-5 5.5" />
          </svg>
        </button>
      </BranchPickerPrimitive.Next>
    </BranchPickerPrimitive.Root>
  );
}

function EditMessageIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M4 14.8 4.7 12 13 3.7a1.4 1.4 0 0 1 2 0l1.3 1.3a1.4 1.4 0 0 1 0 2L8 15.3l-2.8.7Z" />
      <path d="m12 4.7 3.3 3.3" />
    </svg>
  );
}

function CopyMessageIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <rect x="6.5" y="6.5" width="9" height="9" rx="1.5" />
      <path d="M13.5 6.5v-2a1.5 1.5 0 0 0-1.5-1.5H4.5A1.5 1.5 0 0 0 3 4.5V12A1.5 1.5 0 0 0 4.5 13h2" />
    </svg>
  );
}

function CopySuccessIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="m4.5 10.2 3.4 3.4 7.6-7.7" />
    </svg>
  );
}

function ThumbUpIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M6.5 8.2 9.2 3c.3-.6 1-.9 1.6-.6.7.3 1.1 1 1 1.7l-.5 3h3.8c1 0 1.7.9 1.5 1.9l-1 5.3c-.1.8-.8 1.3-1.6 1.3H6.5Z" />
      <path d="M3.4 8.2h3.1v7.4H3.4Z" />
    </svg>
  );
}

function ThumbDownIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="m6.5 11.8 2.7 5.2c.3.6 1 .9 1.6.6.7-.3 1.1-1 1-1.7l-.5-3h3.8c1 0 1.7-.9 1.5-1.9l-1-5.3c-.1-.8-.8-1.3-1.6-1.3H6.5Z" />
      <path d="M3.4 4.4h3.1v7.4H3.4Z" />
    </svg>
  );
}

export { inputArtifactDownloadHref } from "./message-attachment-view";

function HarnessMessageAttachment() {
  const attachment = useAttachment((state) => state);
  return <MessageAttachmentView attachment={attachment} />;
}

type MessageEditorState = {
  messageId: string;
  draft: string;
} | null;

type MessageEditorController = {
  editor: MessageEditorState;
  setEditor: (editor: MessageEditorState) => void;
};

const MessageEditorContext = createContext<MessageEditorController | null>(null);

function useMessageEditor() {
  const controller = useContext(MessageEditorContext);
  if (!controller) throw new Error("Message editor must be rendered inside AgentThread");
  return controller;
}

function HarnessUserMessage() {
  const message = useAuiState((state) => state.message);
  const isLastMessage = useAuiState((state) => state.message.isLast);
  const threadRunning = useAuiState((state) => state.thread.isRunning);
  const activity = useRunActivity();
  const runView = useRunViewModel();
  const isLatestUserMessage = useAuiState((state) => {
    for (let index = state.thread.messages.length - 1; index >= 0; index -= 1) {
      const candidate = state.thread.messages[index];
      if (candidate?.role === "user") return candidate.id === state.message.id;
    }
    return false;
  });
  const thread = useThreadRuntime();
  const { editor, setEditor } = useMessageEditor();
  const originalText = normalizeMessageText(
    message.content
      .filter((part) => part.type === "text")
      .map((part) => part.text)
      .join("\n"),
  );
  const editing = editor?.messageId === message.id;
  const draft = editing ? editor.draft : originalText;

  function beginEdit() {
    setEditor({ messageId: message.id, draft: originalText });
  }

  function submitEdit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = draft.trim();
    if (!text || threadRunning || !isLatestUserMessage) return;
    const parentId =
      message.index > 0 ? thread.getState().messages[message.index - 1]?.id ?? null : null;
    thread.append({
      parentId,
      sourceId: message.id,
      role: "user",
      content: [{ type: "text", text }],
      attachments: message.attachments,
      startRun: true,
    });
    setEditor(null);
  }

  const preResponseActivity =
    activity &&
    activity.run_id === runView?.runId &&
    shouldShowPreResponseActivity(isLastMessage, runView.phase)
      ? activity
      : undefined;

  return (
    <>
      <UserMessage.Root className="harness-user-message" data-turn-id={message.id} data-turn-label={(originalText || message.attachments?.map(item => item.name).join("、") || "附件消息").replace(/\s+/g, " ").slice(0, 180)} tabIndex={-1}>
        <UserMessage.Attachments
          components={{ Attachment: HarnessMessageAttachment }}
        />
        <MessagePrimitive.If hasContent>
          {editing ? (
            <div className="user-message-edit-shell">
              <form className="user-message-editor" onSubmit={submitEdit}>
                <textarea
                  className="user-message-editor-input"
                  aria-label="编辑用户输入"
                  value={draft}
                  onChange={(event) => {
                    setEditor({ messageId: message.id, draft: event.target.value });
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Escape") setEditor(null);
                  }}
                  autoFocus
                  rows={Math.min(8, Math.max(2, draft.split("\n").length))}
                />
                <div className="user-message-editor-actions">
                  <button type="button" onClick={() => setEditor(null)}>取消</button>
                  <button type="submit" disabled={!draft.trim() || threadRunning}>
                    发送
                  </button>
                </div>
              </form>
            </div>
          ) : (
            <>
              <UserMessage.Content />
              <ActionBarPrimitive.Root
                className="harness-user-action-bar"
                autohide="never"
              >
                <MessageCopyButton
                  className="user-message-action"
                  label="复制消息"
                  text={originalText}
                />
                {isLatestUserMessage && !threadRunning ? (
                  <button
                    className="user-message-action"
                    type="button"
                    aria-label="编辑消息"
                    title="编辑消息"
                    onClick={beginEdit}
                  >
                    <EditMessageIcon />
                  </button>
                ) : null}
              </ActionBarPrimitive.Root>
            </>
          )}
        </MessagePrimitive.If>
        <BranchPicker />
      </UserMessage.Root>
      {preResponseActivity ? (
        <div
          className={`latest-activity pre-response-activity ${preResponseActivity.status}`}
          data-activity-source="pre-response"
        >
          <ActivitySummary activity={preResponseActivity} />
        </div>
      ) : null}
    </>
  );
}

export function AgentThread({
  userId,
  threadId,
  agents = [],
  selectedAgent = null,
  agentsLoading = false,
  currentTaskBusy = false,
  activeSkillLaunch = null,
  onDismissSkillLaunch = () => undefined,
  onAgentChange = () => undefined,
  onRefreshAgents,
}: {
  userId: string;
  threadId: string;
  agents?: readonly TaskAgent[];
  selectedAgent?: TaskAgent | null;
  agentsLoading?: boolean;
  currentTaskBusy?: boolean;
  activeSkillLaunch?: SkillCreatorLaunch | null;
  onDismissSkillLaunch?: () => void;
  onAgentChange?: (agent: TaskAgent) => void;
  onRefreshAgents?: () => void;
}) {
  const frame = useRef<HTMLDivElement>(null);
  const [editor, setEditor] = useState<MessageEditorState>(null);
  const composerDraftScope = useMemo(
    () => ({ userId, threadId }),
    [threadId, userId],
  );
  const agentSelection = useMemo(
    () => ({
      agents,
      selected: selectedAgent,
      loading: agentsLoading,
      currentTaskBusy,
      onChange: onAgentChange,
      onRefresh: onRefreshAgents,
    }),
    [agents, agentsLoading, currentTaskBusy, onAgentChange, onRefreshAgents, selectedAgent],
  );
  const skillLaunch = useMemo(
    () => ({ launch: activeSkillLaunch, onDismiss: onDismissSkillLaunch }),
    [activeSkillLaunch, onDismissSkillLaunch],
  );
  return (
    <AgentSelectionContext.Provider value={agentSelection}>
      <SkillLaunchContext.Provider value={skillLaunch}>
        <ComposerDraftContext.Provider value={composerDraftScope}>
          <MessageEditorContext.Provider value={{ editor, setEditor }}>
            <VideoGenerationProvider>
            <div className="harness-thread-frame" ref={frame}>
            <ConversationIndex frame={frame} threadId={threadId} />
            <Thread
            assistantMessage={{
              allowCopy: false,
              allowReload: false,
              allowSpeak: false,
              allowFeedbackPositive: false,
              allowFeedbackNegative: false,
              components: { ToolFallback: HarnessToolPart },
            }}
            userMessage={{ allowEdit: true }}
            branchPicker={{ allowBranchPicker: true }}
            composer={{ allowAttachments: true }}
            components={{
              AssistantMessage: HarnessAssistantMessage,
              UserMessage: HarnessUserMessage,
              Composer: HarnessComposer,
              ThreadWelcome: UserTaskWelcome,
            }}
            strings={{
              thread: { scrollToBottom: { tooltip: "滚动到底部" } },
              userMessage: { edit: { tooltip: "编辑消息" } },
              assistantMessage: {
                reload: { tooltip: "重新运行" },
                copy: { tooltip: "复制回答" },
              },
              branchPicker: {
                previous: { tooltip: "上一个分支" },
                next: { tooltip: "下一个分支" },
              },
              composer: {
                send: { tooltip: "发送任务" },
                cancel: { tooltip: "停止运行" },
                addAttachment: { tooltip: "添加本地文件" },
                removeAttachment: { tooltip: "移除附件" },
                input: { placeholder: "描述任务，或附加文件…" },
              },
              editComposer: { send: { label: "更新" }, cancel: { label: "取消" } },
            }}
            />
            </div>
            </VideoGenerationProvider>
          </MessageEditorContext.Provider>
        </ComposerDraftContext.Provider>
      </SkillLaunchContext.Provider>
    </AgentSelectionContext.Provider>
  );
}
