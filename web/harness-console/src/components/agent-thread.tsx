"use client";
import { responseTextForDisplay } from "../lib/process-response";
import { sendComposerWithRecovery } from "../lib/composer-send";
import { TerminalResponseNotice } from "./terminal-response-notice";
import { FollowupInputPlacement } from "./followup-input-placement";
import { latestConversationInput } from "../lib/conversation-input";
import { useConversationScope } from "../lib/conversation-scope";
import { createThreadHistoryAdapter, useAutoLoadEarlierMessages, useThreadHistoryPagination } from "../lib/task-history";
import { startSteeringPolling } from "../lib/steering-poller";
import { ConversationControl } from "./conversation-control";
import { MessageAttachmentView } from "./message-attachment-view";
import { useThreadHistoryReady } from "./thread-history-ready";
import { ProductBrandMark } from "./product-brand";

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
  type ToolCallMessagePartProps,
} from "@assistant-ui/react";
import {
  useCallback,
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
  ThreadWelcome,
  UserMessage,
} from "@assistant-ui/react-ui";
import { ConversationThread } from "./conversation-thread";
import { ConversationIndex } from "./conversation-index";
import { useRunCancel } from "./run-cancel-context";
import { PromptQueue } from "./prompt-queue";
import { useFollowUpPreference } from "../lib/interface-preferences";
import { useConversationRedactor } from "./conversation-redaction";
import { SentUserContent } from "./sent-user-content";
import { ConversationInput } from "./conversation-input";
import { groupWorkspaceFiles } from "../lib/workspace-file-groups";
import { ActivitySummary } from "./activity-summary";
import { TaskAgentSwitcher } from "./task-agent-switcher";
import { ApprovalCard, type ApprovalDetails } from "./approval-card";
import { ArtifactCard, isImageArtifact, type ArtifactDetails } from "./artifact-list";
import { ReplyArtifactImages } from "./reply-artifact-images";
import { ReplyArtifactVideos } from "./reply-artifact-videos";
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
import { AUTHORING_PROGRESS_PART_NAME, AuthoringProgressPart } from "./authoring-progress";
import { normalizeMessageText } from "../lib/message-text";
import { inputArtifactIdFromAttachment } from "../lib/input-attachment-adapter";
import type { TaskAgent } from "../lib/task-agent-catalog";
import {
  VIDEO_GENERATION_PART_NAME,
  VideoGenerationControls,
  DurableVideoGenerationPart,
  VideoGenerationMessagePart,
  VideoGenerationProvider,
  useVideoGeneration,
} from "./video-generation";

import { createRandomId } from "../lib/random-id";
import { ComposerAssist, composerOptions } from "./composer-assist";
import {
  TaskKnowledgeControl,
  TaskKnowledgeSelection,
  useTaskKnowledge,
} from "./task-knowledge-context";
import { composerTrigger, queueAttachments, queueMayDispatch, restorePromptQueue, type QueuedPrompt } from "../lib/composer-interactions";

import { writeTextToClipboard } from "../lib/clipboard";
import { CodexResponseIcon } from "./codex-response-icon";
import { useResponseMotion } from "./use-response-motion";

export { normalizeMessageText } from "../lib/message-text";
import { runReuseStore, useRunReuseNotice } from "../lib/run-reuse-store";
import {
  IntelligentUiBlock,
  type PresentationActionHandler,
} from "./intelligent-ui/presentation-host";
import { submitUiAction } from "./intelligent-ui/client";
import { modelBlockSources, presentationsForMessage } from "./intelligent-ui/schema";
import { PublishedBlocksProvider } from "./intelligent-ui/turn-context";
import { consumeProjectTaskLaunch } from "../lib/project-task-launch";
import { useProjectTaskLaunch } from "./use-project-task-launch";
import { useTaskComposerSnapshot } from "./task-composer-draft";
import { VoiceInput } from "./voice-input";
import { useDictationComposer } from "./use-dictation-composer";
import { HarnessComposerAttachment } from "./composer-attachment";
import {
  skillCreatorPrompt,
  type SkillCreatorLaunch,
} from "../lib/skill-creator-launch";

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
  return writeTextToClipboard(normalizeMessageText(value));
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

  const redact = useConversationRedactor();
  async function copy() {
    const copied = await writeMessageToClipboard(redact(text));
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
      <CodexResponseIcon name={copyState === "copied" ? "check" : "copy"} />
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
        <CodexResponseIcon name="thumbUp" />
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
        <CodexResponseIcon name="thumbDown" />
      </button>
      <span className="message-copy-status" aria-live="polite">{error}</span>
    </>
  );
}

function HarnessComposer({ onSubmitted }: { onSubmitted?: () => void } = {}) {
  const conversationScope = useConversationScope();
  const aui = useAui();
  const { routes, overrideRouteId } = useTaskModel();
  const threadRunning = useAuiState((state) => state.thread.isRunning);
  const threadLoading = useAuiState((state) => state.thread.isLoading);
  const composerCanSend = useAuiState((state) => state.composer.canSend);
  const messages = useAuiState((state) => state.thread.messages);
  const requestedInput = useMemo(() => latestConversationInput(messages), [messages]);
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
  const projectLaunchScope = useContext(ComposerDraftContext);
  const projectLaunchReady = useThreadHistoryReady();
  useTaskComposerSnapshot(projectLaunchScope);
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
      if (!conversationScope) approvalStore.settle(visibleApprovalId);
    }
  }, [
    pendingApproval.details?.approval_id,
    pendingApproval.visible,
    runView?.pendingApprovalId,
    conversationScope,
  ]);
  const showStop = shouldShowComposerStop(
    threadRunning,
    stream.status,
    runView?.phase,
  );
  const videoGenerating = videoGeneration.generating;
  const threadRuntime = useThreadRuntime();
  const cancelActiveRun = useRunCancel();
  const scope = useContext(ComposerDraftContext);
  const queueKey = scope ? `harness:prompt-queue:${scope.userId}:${scope.threadId}` : null;
  const [queue, setQueue] = useState<QueuedPrompt[]>([]);
  const [followUpBehavior] = useFollowUpPreference();
  const [queueLoaded, setQueueLoaded] = useState(false);
  const [queuePaused, setQueuePaused] = useState(false);
  const [inputError, setInputError] = useState("");
  const [voiceActive, setVoiceActive] = useState(false);
  const [followupVoiceActive, setFollowupVoiceActive] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [caret, setCaret] = useState(0);
  const [suggestionIndex, setSuggestionIndex] = useState(0);
  const [dismissedText, setDismissedText] = useState<string | null>(null);
  const dispatching = useRef(false);
  const [steerAvailable, setSteerAvailable] = useState(false);
  const [steeringIds, setSteeringIds] = useState<string[]>([]);
  const [steeringNotice, setSteeringNotice] = useState("");
  const [stopPending, setStopPending] = useState(false);
  const stopInFlight = useRef(false);
  const steeringSentAt = useRef<Record<string, number>>({});
  const steeringRunId = queue.find((item) => item.steerRunId)?.steerRunId ?? runView?.runId;
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const composingRef = useRef(false);
  const voiceComposer = useDictationComposer(inputRef, () => aui.composer().getState().text,
    (text) => aui.composer().setText(text), composingRef);
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
    conversationScope ? [] : knowledge.available,
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
    else if (value === "/files") {if (conversationScope) conversationScope.onOpenFiles();else window.dispatchEvent(new Event("harness:open-files"));}
    else if (value === "/help") setHelpOpen((current) => !current);
    else if (value === "/new") {if (conversationScope) conversationScope.onNew();else window.dispatchEvent(new Event("harness:new-task"));}
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
      if (steer && !next.attachments.length) await guide(next);

    } catch (error) { setInputError(error instanceof Error ? error.message : "无法加入队列"); }
  }
  const hasPendingSteering = steeringIds.length > 0;
  const steeringContext = useRef({ runId: runView?.runId, busy, steeringIds });
  steeringContext.current = { runId: runView?.runId, busy, steeringIds };
  useEffect(() => {
    if (!steeringRunId || (!busy && !hasPendingSteering)) { setSteerAvailable(false); return; }
    let available = false;
    return startSteeringPolling({
      runId: steeringRunId,
      shouldPoll: () => (steeringContext.current.busy && !available) || steeringContext.current.steeringIds.length > 0,
      onState: (state) => {
        const steeringIds = steeringContext.current.steeringIds;
        available = state.available;
        setSteerAvailable(state.available && steeringRunId === steeringContext.current.runId);
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
            setSteeringNotice("");
          } else if (["failed", "not_delivered", "unknown"].includes(item.status)) {
            setSteeringIds((ids) => ids.filter((id) => id !== item.request_id));
            setQueuePaused(true);
            setQueue((items) => items.map((entry) => entry.id === item.request_id ? { ...entry, steerRunId: undefined } : entry));
            setInputError(item.status === "unknown" ? "引导接收状态不确定，请先查看回复再决定是否重发。" : item.error || "运行已经结束，补充仍保留在队列中。");
          }
        }
      },
      onError: () => {
        setSteerAvailable(false);
        if (steeringContext.current.steeringIds.length) {
          setQueuePaused(true);
          setInputError("暂时无法核对引导状态；补充已保留，请刷新后核对，避免重复发送。");
        }
      },
    });
  }, [steeringRunId, busy, hasPendingSteering]);

  async function guide(item: QueuedPrompt) {
    if (!runView?.runId || steeringIds.includes(item.id)) return;
    if (item.attachments.length) { setInputError("带附件的补充请加入队列；实时引导目前支持文本。"); return; }
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
        setQueuePaused(true);
        setSteeringIds((ids) => ids.filter((id) => id !== item.id));
        setQueue((items) => items.map((entry) => entry.id === item.id ? { ...entry, steerRunId: undefined } : entry));
        setInputError("当前运行无法接收引导，内容仍保留在队列中。");
      }
    } catch { setInputError("引导请求状态待确认，正在核对接收结果；请勿重复发送。"); }
  }
  useEffect(() => {
    if (queueLoaded && queue.length === 0 && steeringIds.length === 0) setQueuePaused(false);
  }, [queueLoaded, queue.length, steeringIds.length]);
  useEffect(() => {
    if (!showStop) {
      if (stopInFlight.current) setSteeringNotice("");
      stopInFlight.current = false; setStopPending(false);
    }
  }, [showStop]);
  async function stopRun() {
    if (stopInFlight.current || !showStop) return;
    stopInFlight.current = true;
    setStopPending(true);
    setQueuePaused(true);
    setInputError("");
    setSteeringNotice("正在停止运行…");
    try {
      if (!runView?.runId || ["completed", "failed", "rejected", "cancelled"].includes(runView.phase)) {
        if (cancelActiveRun) { await cancelActiveRun(); return; }
        throw new Error("尚未取得运行标识，请稍后重试。");
      }
      const response = requireAuthenticatedResponse(await fetch(`/api/agui/runs/${encodeURIComponent(runView.runId)}/cancel`, { method: "POST" }));
      if (!response.ok) throw new Error("停止请求未成功，请重试。");
      // Keep the live stream until the server publishes its terminal state.
      // The HTTP acceptance is not proof that execution has stopped yet.
    } catch (error) {
      stopInFlight.current = false;
      setStopPending(false);
      setSteeringNotice("");
      setInputError(error instanceof Error ? error.message : "停止请求未成功，请重试。");
    }
  }
  const composerSendLock = useRef(false);
  async function submitComposer(alternate = false) {
    if (voiceActive || followupVoiceActive) return;
    if (projectLaunchScope) consumeProjectTaskLaunch(projectLaunchScope.userId, projectLaunchScope.threadId);
    if (command(composerText.trim())) return;
    if (videoRoute) { void generateVideo(); return; }
    if (busy || queue.length) {
      const steer = alternate ? followUpBehavior !== "steer" : followUpBehavior === "steer";
      void enqueue(busy && steer); return;
    }
    if (composerSendLock.current) return;
    composerSendLock.current = true;
    setInputError("");
    try {
      const error = await sendComposerWithRecovery(threadRuntime.composer);
      if (error) setInputError(error);
      else onSubmitted?.();
    } catch (error) {
      setInputError(error instanceof Error ? error.message : "发送失败，请重试。");
    } finally { composerSendLock.current = false; }
  }

  useProjectTaskLaunch(
    projectLaunchScope,
    projectLaunchReady && !threadLoading && !agentSelection.loading &&
      queueLoaded && !busy && !queue.length && !voiceActive && !followupVoiceActive &&
      composerCanSend && Boolean(composerText.trim()),
    async () => {
      if (composerSendLock.current || !threadRuntime.composer.getState().canSend) return false;
      composerSendLock.current = true;
      try {
        const error = await sendComposerWithRecovery(threadRuntime.composer);
        if (error) {
          setInputError(error);
          // A real send failure stays visible for an explicit retry, never a loop.
          if (projectLaunchScope) consumeProjectTaskLaunch(projectLaunchScope.userId, projectLaunchScope.threadId);
          return false;
        }
        onSubmitted?.();
        return true;
      } catch (error) {
        setInputError(error instanceof Error ? error.message : "项目任务未能发送，输入已保留，请重试。");
        if (projectLaunchScope) consumeProjectTaskLaunch(projectLaunchScope.userId, projectLaunchScope.threadId);
        return false;
      } finally { composerSendLock.current = false; }
    },
  );

  async function generateVideo() {
    const prompt = composerText.trim();
    if (!videoRoute || !prompt || videoGenerating) return;
    setVideoValidationError(null);
    if (composerAttachments.some((attachment) => attachment.type !== "image")) {
      setVideoValidationError("视频参考素材只支持图片，请移除文档或其他文件。");
      return;
    }
    if (videoGeneration.settings.mode === "ref2va" && composerAttachments.length === 0) {
      setVideoValidationError("Ref2VA 至少需要添加一张参考图片。");
      return;
    }
    const officialVideo = videoRoute.apiFormat === "minimax_videos";
    const maximumReferences = officialVideo ? 1 : videoGeneration.settings.mode === "ref2va" ? 9 : 2;
    if (composerAttachments.length > maximumReferences) {
      setVideoValidationError(
        officialVideo ? "官方视频最多使用一张首帧图片。" : videoGeneration.settings.mode === "ref2va"
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
      apiFormat: videoRoute.apiFormat,
      model: videoRoute.model,
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
      {conversationScope?.composerAccessory}
      {requestedInput && !conversationScope?.composerAccessory && <FollowupInputPlacement key={requestedInput.messageId} messageId={requestedInput.messageId} input={requestedInput.input} modelRoute={overrideRouteId || ""} onVoiceActive={setFollowupVoiceActive} disabled={runLocked || busy || threadRunning || voiceActive} onSubmit={answer => {
        threadRuntime.append({ role: "user", content: [{type: "text", text: answer}] });
      }} />}
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
            key={pendingApproval.details.approval_id}
            details={pendingApproval.details}
            complete={false}
            onDecision={async (decision) => {
              const approvalId = pendingApproval.details!.approval_id;
              if (conversationScope) {await conversationScope.onApproval(approvalId, decision);return;}
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
      <TaskModelVisionNotice
        disabled={runLocked || showStop || videoGenerating}
        requiresVision={composerAttachments.some((attachment) => attachment.type === "image")}
      />
      {videoRoute ? (
        <>
          <VideoGenerationControls
            label={videoRoute.label}
            routeId={videoRoute.id}
            apiFormat={videoRoute.apiFormat}
            model={videoRoute.model}
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
      <PromptQueue items={queue.filter(item => !item.steerRunId || !runView?.items.some(event =>
        ["run.steer.requested", "run.steer.accepted"].includes(event.event_type) && event.metadata.request_id === item.id))} paused={queuePaused} busy={busy} canSteer={steerAvailable} sendingIds={steeringIds}
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
      {!conversationScope?.compactComposer && <TaskKnowledgeSelection disabled={busy || Boolean(conversationScope)} />}
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
          <p>运行中 Enter {followUpBehavior === "steer" ? "补充说明" : "加入队列"}，Alt Enter 使用另一种方式，Shift Enter 换行。可在个人设置的配置中更改默认行为。</p>
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
        <Composer.Attachments components={{ Attachment: HarnessComposerAttachment }} />
        <div
          className="composer-input-wrap"
        >
        <ConversationInput
          ref={inputRef}
          onComposingChange={(value) => { composingRef.current = value; }}
          className="aui-composer-input"
          aria-label="消息输入"
          placeholder={conversationScope?.composerPlaceholder ?? (busy ? "继续补充…" : knowledge.selected.length ? "输入问题，将基于上方选中的知识库回答" : "随心输入，/ 命令 · @ 知识库 · $ 技能")}
          rows={Math.min(8, Math.max(2, composerText.split("\n").length))}
          aria-controls={options.length ? "composer-suggestions" : undefined}
          aria-activedescendant={options.length ? `composer-option-${suggestionIndex}` : undefined}
          onChange={(event) => { voiceComposer.onChange(event.target.value); if (!composingRef.current) setCaret(event.target.selectionStart); }}
          onCompositionEnd={(event) => setCaret(event.currentTarget.selectionStart)}
          onSelect={(event) => setCaret(event.currentTarget.selectionStart)}
          onPaste={(event) => {
            const files = Array.from(event.clipboardData.files);
            if (!files.length) return;
            event.preventDefault();
            setInputError("");
            for (const file of files) void threadRuntime.composer.addAttachment(file).catch((error: unknown) => {
              const reason = error instanceof Error ? error.message : "附件添加失败，请重试。";
              setInputError((previous) => [previous, `${file.name}：${reason}`].filter(Boolean).join("\n"));
            });
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
        </div>
        <div className="composer-footer">
        <div className="composer-toolbar">
          <Composer.AddAttachment>
            <svg className="aui-composer-attach-icon" viewBox="0 0 20 20" aria-hidden="true">
              <path d="M10 4.5v11M4.5 10h11" />
            </svg>
          </Composer.AddAttachment>

          {!conversationScope?.compactComposer && <>{conversationScope ? <button type="button" className="aui-composer-attach" aria-label="配置智能体知识库" title="配置智能体知识库" onClick={conversationScope.onConfigureKnowledge}>@</button> : <TaskKnowledgeControl disabled={runLocked || showStop || videoGenerating} />}
          <TaskAgentSwitcher
            agents={agentSelection.agents}
            selected={agentSelection.selected}
            loading={agentSelection.loading}
            currentTaskBusy={agentSelection.currentTaskBusy}
            onChange={agentSelection.onChange}
            onRefresh={agentSelection.onRefresh}
          />
          </>}
          <div className="composer-model-voice">
          {!conversationScope?.compactComposer && <TaskModelControl disabled={Boolean(conversationScope) || runLocked || showStop || videoGenerating} />}
          <VoiceInput key={scope?.threadId || "new"} disabled={busy || followupVoiceActive}
            modelRoute={overrideRouteId || ""}
            onActive={(active) => { voiceComposer.onActive(active); setVoiceActive(active); }}
            onDraft={voiceComposer.onDraft} onInsert={voiceComposer.onInsert} />
          </div>
        </div>
        {showStop && !composerText.trim() && !composerAttachments.length ? (
          <ConversationControl action="stop" aria-label={stopPending ? "正在停止运行" : "停止运行"} disabled={stopPending} onClick={() => void stopRun()} />
        ) : videoRoute ? (
          <ConversationControl action="send" disabled={voiceActive || followupVoiceActive || videoGenerating || !composerText.trim()}
            aria-label={videoGenerating ? "视频生成中" : "生成视频"}
            onClick={() => void generateVideo()} />
        ) : (
          <ConversationControl action="send" aria-label={busy ? followUpBehavior === "steer" && steerAvailable && !composerAttachments.length ? "补充说明" : "加入队列" : queue.length ? "加入队列" : "发送消息"} title={busy ? `Enter ${followUpBehavior === "steer" ? "补充说明" : "加入队列"} · Alt Enter 切换` : "发送消息"} disabled={voiceActive || followupVoiceActive || (!composerText.trim() && !composerAttachments.length)} onClick={() => submitComposer()} />
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


function ApprovalToolBridge({
  details,
  complete,
}: {
  details: ApprovalDetails;
  complete: boolean;
}) {
  const conversationScope = useConversationScope();
  useEffect(() => {
    if (conversationScope) return;
    if (complete) approvalStore.settle(details.approval_id);
    else approvalStore.show(details);
  }, [complete, details, conversationScope]);
  if (conversationScope && !complete) return <ApprovalCard details={details} complete={false} onDecision={decision => conversationScope.onApproval(details.approval_id, decision)} />;
  return null;
}

export function UserTaskWelcome() {
  const scope = useConversationScope();
  const { selected } = useContext(AgentSelectionContext);
  // While the stored conversation is loading the thread is empty by definition,
  // so the welcome would flash and be replaced a moment later.
  if (!useThreadHistoryReady()) return null;
  return (
    <ThreadWelcome.Root className="user-task-welcome">
      <ThreadWelcome.Center className="user-task-hero">
        <div className="user-task-intro">
          <ProductBrandMark className="welcome-brand-mark" />
          <h1>{scope && selected ? <>今天想让<span className="welcome-agent-name">{selected.displayName}</span>帮你做些什么？</> : "今天想一起完成些什么？"}</h1>
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
  const live = useLiveResponse();
  const messageId = useAuiState((state) => state.message.id);
  const hasText = useAuiState((state) =>
    state.message.content.some(
      (part) => part.type === "text" && part.text.trim().length > 0,
    ),
  );
  // Earlier progress prose must not mark later thinking as final-answer output.
  return live.messageId === messageId && live.status !== "idle" ? live.visible : hasText;
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

/** The artifacts this turn presents. They are summarised once, above the answer's
 * actions, and the drawer the summary opens is where they are actually browsed. */
export function artifactsForTurn(
  parts: readonly { type?: string; toolName?: string; args?: unknown }[],
  viewRunId: string | undefined,
  isLast: boolean,
): ArtifactDetails[] {
  return parts
    .filter((part) => part.type === "tool-call" && part.toolName === "harness_present_artifact")
    .filter((part) => {
      const args = (part.args ?? {}) as Record<string, unknown>;
      return shouldShowArtifactForTurn(
        typeof args.run_id === "string" ? args.run_id : undefined,
        viewRunId,
        isLast,
      );
    })
    .map((part) => (part.args ?? {}) as ArtifactDetails);
}

function artifactMark(name: string | undefined, mediaType: string | undefined) {
  const type = (mediaType ?? "").toLowerCase();
  if (type.startsWith("image/")) return "IMG";
  if (type === "application/pdf") return "PDF";
  if (type.includes("json")) return "JSON";
  if (type.includes("markdown")) return "MD";
  if (type.startsWith("video/")) return "VID";
  const extension = (name ?? "").split(".").pop()?.toLowerCase() ?? "";
  return extension ? extension.slice(0, 4).toUpperCase() : "FILE";
}

const ARTIFACT_THUMBNAIL_COUNT = 3;

/** One row above the answer's actions: a few thumbnails and the count, opening
 * the task's file drawer. The row stays visible at all times; only its inner
 * thumbnails cap at three with a +N chip, which is how it always looked. */
function ArtifactSummaryRow({ artifacts }: { artifacts: ArtifactDetails[] }) {
  const conversationScope = useConversationScope();
  const [failed, setFailed] = useState<readonly string[]>([]);
  if (artifacts.length === 0) return null;
  const groups = groupWorkspaceFiles(artifacts.map(file => ({ ...file, name: file.name ?? "未命名文件", media_type: file.media_type ?? "" })));
  const deliverables = [...(groups.find(group => group.name === "最终产出")?.folders.values() ?? [])].flat();
  if (deliverables.length === 0) return null;
  const thumbs = deliverables.slice(0, ARTIFACT_THUMBNAIL_COUNT);
  const extra = deliverables.length - thumbs.length;
  const countLabel = `${deliverables.length} 项产出`;
  const label = `查看本任务的 ${countLabel}`;
  return (
    <button
      type="button"
      className="artifact-summary-row"
      aria-label={label}
      title={label}
      onClick={() => conversationScope ? conversationScope.onOpenFiles() : window.dispatchEvent(new CustomEvent("harness:open-files"))}
    >
      <span className="artifact-summary-thumbs" aria-hidden="true">
        {thumbs.map((details) => (
          <span className="artifact-thumb" key={details.artifact_id} data-mark={artifactMark(details.name, details.media_type)}>
            {isImageArtifact(details) && !failed.includes(details.artifact_id) ? (
              // Same-origin artifact endpoint; a failed image falls back to the mark.
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={`/api/harness/artifacts/${encodeURIComponent(details.artifact_id)}?preview=1`}
                alt=""
                loading="lazy"
                onError={() => setFailed((current) => [...current, details.artifact_id])}
              />
            ) : null}
            <b>{artifactMark(details.name, details.media_type)}</b>
          </span>
        ))}
        {extra > 0 ? <span className="artifact-thumb artifact-thumb-more">+{extra}</span> : null}
      </span>
      <span className="artifact-summary-count">{countLabel}</span>
    </button>
  );
}

function HarnessToolPart(part: ToolCallMessagePartProps) {
  const status = toolStatus(part);
  const args = objectValue(part.args);
  const runView = useRunViewModel();
  if (part.toolName === "harness_video_generation") return <DurableVideoGenerationPart data={args} />;
  if (part.toolName === "harness_run_activity") {
    return null;
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
    return <HarnessArtifactPart />;
  }
  if (part.toolName === "harness_present_ui") {
    // Rendered once, below the answer, by the turn's presentation list.
    return null;
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

function HarnessArtifactPart() {
  // One summary row carries the turn's files, so no card per file duplicates it.
  return null;
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
  "harness_video_generation",
  "harness_run_activity",
  "harness_present_artifact",
  "harness_present_ui",
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

// All text sources share this slot. Switching a live answer to durable history
// must update props, not unmount Markdown (which also resets expanded blocks).
function HiddenAssistantText() { return null; }

export function AssistantResponse({ text, streaming }: { text: string; streaming: boolean }) {
  const response = useRef<HTMLDivElement>(null);
  useResponseMotion(response, Boolean(text));
  if (!text) return null;
  return (
    <div ref={response} className="assistant-answer" data-streaming={streaming ? "true" : "false"} aria-busy={streaming}>
      <TextMessagePartProvider text={text} isRunning={streaming}>
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
  const content = useAuiState((state) => state.message.content);
  const durablePart = hasDurableProjection
    ? content.find((part) => part.type === "tool-call" && part.toolName === "harness_run_activity")
    : undefined;
  const parsed = durablePart?.type === "tool-call"
    ? runActivitySchema.safeParse(durablePart.args.activity)
    : null;
  const durableActivity = parsed?.success ? parsed.data : undefined;
  const ownedActivity = activity && turnOwnsRun(
    messageId,
    activity.run_id,
    isLast,
    ["running", "queued", "waiting_approval"].includes(runView?.phase ?? "") ? runView?.runId : undefined,
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
  const displayed = selectActivityForTurn(durableActivity, ownedActivity, capturedActivity, isLast, hasDurableProjection);
  if (
    !displayed ||
    (!durableActivity && !turnOwnsRun(messageId, displayed.run_id, isLast, runView?.runId))
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

export function selectActivityForTurn(
  durable: RunActivity | undefined,
  current: RunActivity | undefined,
  captured: RunActivity | undefined,
  isLast: boolean,
  hasDurableProjection: boolean,
) {
  return durable ?? selectTurnActivity(current, captured, isLast, hasDurableProjection);
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

/** Both streams must settle before presenting this turn's deliverables. */
export function turnDeliverablesReady(messageStatus: string | undefined, ownedRunPhase?: string) {
  return messageStatus !== "running" && (
    !ownedRunPhase || ["completed", "failed", "rejected", "cancelled"].includes(ownedRunPhase)
  );
}

export function shouldSuppressNativeAssistantText(
  ownsLive: boolean,
  live: Pick<LiveResponseSnapshot, "status" | "visible" | "text">,
  awaitingMessage = false,
) {
  // Retain ownership of hidden progress until the durable history hand-off.
  // Only an empty stream can fall back to native text without duplicating prose.
  return awaitingMessage || (ownsLive && (live.status === "streaming" || Boolean(live.text.trim())));
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
    messageId.startsWith("__optimistic__") && isLast && viewRunId === activityRunId
  );
}

type CapturedTurnResponse = { messageId: string; text: string };

/** Keep the cleaned live projection when a turn stops being the latest one.
 * The native stream may still contain pre-tool prose until history commits. */
export function responseTextForTurn(messageId: string, nativeText: string, directStream: boolean,
  live: Pick<LiveResponseSnapshot, "text" | "visible" | "status">,
  captured: { current: CapturedTurnResponse | undefined }, awaitingMessage = false) {
  if (captured.current?.messageId !== messageId) captured.current = undefined;
  // A new optimistic turn may mount before runAgent resets the global store.
  // Never borrow the previous turn's visible text during that interval.
  if (awaitingMessage) {
    captured.current = { messageId, text: "" };
    return "";
  }
  if (directStream) {
    const text = normalizeMessageText(live.visible ? live.text : "");
    captured.current = { messageId, text };
    return text;
  }
  if (captured.current) {
    if (nativeText === captured.current.text || (live.status === "idle" && !captured.current.text)) captured.current = undefined;
    else return captured.current.text;
  }
  return nativeText;
}

export function HarnessAssistantMessage() {
  const redact = useConversationRedactor();
  const conversationScope = useConversationScope();
  const live = useLiveResponse();
  const isLast = useAuiState((state) => state.message.isLast);
  const messageId = useAuiState((state) => state.message.id);
  const messageStatus = useAuiState((state) => state.message.status);
  const runView = useRunViewModel();
  const threadRuntime = useThreadRuntime({ optional: true });
  const showIncompleteRecovery = shouldOfferIncompleteRetry(messageStatus);
  const content = useAuiState((state) => state.message.content);
  const hasVideoGeneration = content.some(
    (part) => (part.type === "data" && part.name === VIDEO_GENERATION_PART_NAME) || (part.type === "tool-call" && part.toolName === "harness_video_generation"),
  );
  // Own the native text slot as soon as a Harness message starts. Candidate
  // text may still be waiting to see whether a tool call follows, so basing
  // this only on visible text lets assistant-ui paint the same preface once.
  const ownsLive = ownsLiveResponse(isLast, messageId, live.messageId);
  const pendingLive = isLast && messageStatus?.type === "running" &&
    live.status !== "idle" && !ownsLive;
  const directStream = shouldSuppressNativeAssistantText(ownsLive, live, pendingLive);
  const capturedResponse = useRef<CapturedTurnResponse | undefined>(undefined);
  const nativeText = normalizeMessageText(
    content.flatMap((part, index) => (
      part.type === "text" && !isIntermediateAssistantTextPart(content, index) ? [part.text] : []
    )).join("\n"),
  );
  const responseCandidate = responseTextForTurn(messageId, nativeText, directStream, live, capturedResponse, pendingLive);
  const feedbackRun = feedbackRunId(messageId, isLast, runView?.runId);
  // Requirement: citations must be reachable from the reply itself, not only
  // from the collapsed execution summary. Deduplicate by chunk so repeated
  // retrieval in one run shows one chip per slice.
  const durablePart = content.find((part) => part.type === "tool-call" && part.toolName === "harness_run_activity");
  const durable = durablePart?.type === "tool-call" ? runActivitySchema.safeParse(durablePart.args.activity) : null;
  const currentActivity = useRunActivity();
  const ownedActivity = currentActivity && turnOwnsRun(messageId, currentActivity.run_id, isLast, runView?.runId)
    ? currentActivity : undefined;
  const terminalActivity = ownedActivity && (!durable?.success || durable.data.run_id === ownedActivity.run_id)
    ? ownedActivity : durable?.success ? durable.data : undefined;
  const copyText = redact(responseTextForDisplay(responseCandidate, terminalActivity));
  const capturedCitations = useRef<{ messageId: string; citations: RunCitation[] }>({ messageId, citations: [] });
  const turnCitations = citationsForTurn(messageId, isLast, runView, durable?.success ? durable.data : undefined);
  if (turnCitations) capturedCitations.current = { messageId, citations: turnCitations };
  const answerCitations = !hasVideoGeneration && capturedCitations.current.messageId === messageId ? capturedCitations.current.citations : [];
  const ownsActiveRun = Boolean(runView && turnOwnsRun(messageId, runView.runId, isLast, runView.runId));
  const turnSettled = turnDeliverablesReady(messageStatus?.type, ownsActiveRun ? runView?.phase : undefined);
  const turnArtifacts = useMemo(
    () => turnSettled ? artifactsForTurn(content, runView?.runId, isLast) : [],
    [content, runView?.runId, isLast, turnSettled],
  );
  // Interactive answer blocks arrive on the same tool-call channel artifacts do,
  // so they are present live and after a reload without a second code path.
  const turnPresentations = useMemo(() => turnSettled ? presentationsForMessage(content) : [], [content, turnSettled]);
  // Only blocks the model authored came from a fence in this answer, so only that
  // many fences may be hidden from the rendered Markdown.
  const publishedBlocks = useMemo(() => modelBlockSources(turnPresentations), [turnPresentations]);
  const draftScope = useContext(ComposerDraftContext);
  const runPresentationAction: PresentationActionHandler | undefined = threadRuntime && !conversationScope ? submitUiAction : undefined;
  const actionHistory = useMemo(() => draftScope ? createThreadHistoryAdapter(draftScope.threadId) : null, [draftScope]);
  useEffect(() => () => actionHistory?.dispose(), [actionHistory]);
  const refreshActionHistory = useCallback(() => {
    if (!actionHistory || !threadRuntime || threadRuntime.getState().isRunning) return;
    void actionHistory.loadSnapshot(repository => {
      if (threadRuntime.getState().isRunning) return false;
      threadRuntime.import(repository);
    }).catch(() => { /* persisted history remains available on refresh */ });
  }, [actionHistory, threadRuntime]);
  const actionBusy = ["running", "queued", "waiting_approval"].includes(runView?.phase ?? "");
  return (
    <AnswerCitationProvider citations={answerCitations}>
    <PublishedBlocksProvider sources={publishedBlocks}>
    <AssistantMessage.Root
      className="harness-assistant-message"
      data-test-run={conversationScope && messageId.startsWith("assistant-") ? messageId.replace(/^assistant-/, "") : undefined}
      data-turn-answer={copyText.replace(/\s+/g, " ").slice(0, 360)}
      data-direct-stream={directStream ? "true" : "false"}
    >
      {!hasVideoGeneration ? <TurnActivity
        hasDurableProjection={hasRunActivityToolCall(content)}
        messageId={messageId}
      /> : null}
      <AssistantMessage.Content
        components={{
          Text: HiddenAssistantText,
          Reasoning: ReasoningPart,
          data: {
            by_name: {
              [VIDEO_GENERATION_PART_NAME]: VideoGenerationMessagePart,
              [AUTHORING_PROGRESS_PART_NAME]: AuthoringProgressPart,
            },
          },
        }}
      />
      <AssistantResponse key={messageId} text={copyText} streaming={directStream ? live.status === "streaming" && !live.textComplete : messageStatus?.type === "running"} />
      <div className="followup-input-anchor" data-followup-for={messageId} />
      {!hasVideoGeneration && <TerminalResponseNotice activity={terminalActivity}
        hasResponse={Boolean(copyText.trim()) || turnPresentations.length > 0 || turnArtifacts.length > 0} />}
      {turnPresentations.length > 0 ? (
        <div className="intelligent-ui-blocks">
          {turnPresentations.map((presentation) => (
            <IntelligentUiBlock
              key={presentation.presentation_id}
              value={presentation}
              onAction={runPresentationAction}
              connected={!conversationScope && Boolean(threadRuntime)}
              onSettled={refreshActionHistory}
              disabled={actionBusy}
            />
          ))}
        </div>
      ) : null}
      <ReplyArtifactImages artifacts={turnArtifacts} answer={copyText}
        onPreview={conversationScope ? () => conversationScope.onOpenFiles() : undefined} />
      {!hasVideoGeneration ? <ReplyArtifactVideos artifacts={turnArtifacts} /> : null}
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
        <>
        <div className="artifact-summary-line">
          <ArtifactSummaryRow artifacts={turnArtifacts} />
        </div>
        <div className="assistant-message-controls">
          <HarnessBranchPicker />
          <AssistantActionBar.Root
            className="assistant-feedback-actions"
            hideWhenRunning
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
        </>
      ) : null}
      {conversationScope?.afterMessage(messageId)}
    </AssistantMessage.Root>
    </PublishedBlocksProvider>
    </AnswerCitationProvider>
  );
}

function TurnCompletion() {
  const content = useAuiState((state) => state.message.content);
  const isLast = useAuiState((state) => state.message.isLast);
  const observed = useRunViewModel();
  const messageId = useAuiState((state) => state.message.id);
  const durable = content.find((part) => part.type === "tool-call" && part.toolName === "harness_run_activity");
  const parsed = durable?.type === "tool-call" ? runActivitySchema.safeParse(durable.args.activity) : null;
  const activity = parsed?.success ? parsed.data : null;
  const view = activity ? reduceRunViewModel(undefined, activity) : isLast && observed && messageOwnsRun(messageId, observed.runId) ? observed : null;
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
  const redact = useConversationRedactor();
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
  const displayText = redact(originalText);
  const sensitive = displayText !== originalText;
  const editing = !sensitive && editor?.messageId === message.id;
  const draft = editing && editor ? editor.draft : displayText;

  function beginEdit() {
    if (sensitive) return;
    setEditor({ messageId: message.id, draft: displayText });
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
      <UserMessage.Root className="harness-user-message" data-turn-id={message.id} data-turn-label={(displayText || message.attachments?.map(item => item.name).join("、") || "附件消息").replace(/\s+/g, " ").slice(0, 180)} tabIndex={-1}>
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
              <SentUserContent key={message.id} text={sensitive ? displayText : undefined} />
              <ActionBarPrimitive.Root
                className="harness-user-action-bar"
                autohide="never"
              >
                <MessageCopyButton
                  className="user-message-action"
                  label="复制消息"
                  text={displayText}
                />
                {isLatestUserMessage && !threadRunning && !sensitive ? (
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
        {editing ? null : <BranchPicker />}
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
  onComposerSubmitted,
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
  /** Called after the composer successfully starts a run. */
  onComposerSubmitted?: () => void;
}) {
  const frame = useRef<HTMLDivElement>(null);
  const [editor, setEditor] = useState<MessageEditorState>(null);
  // Optional: AgentThread is also rendered by config tests without a runtime
  // provider; without a runtime there is nothing to import earlier pages into.
  const threadRuntime = useThreadRuntime({ optional: true });
  const historyPagination = useThreadHistoryPagination(threadId, {
    importRepository: (repository) => threadRuntime?.import(repository),
  });
  useAutoLoadEarlierMessages(frame, historyPagination);
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
  const ComposerComponent = useMemo(
    () =>
      onComposerSubmitted
        ? function SubmittedComposer() {
            return <HarnessComposer onSubmitted={onComposerSubmitted} />;
          }
        : HarnessComposer,
    [onComposerSubmitted],
  );
  return (
    <AgentSelectionContext.Provider value={agentSelection}>
      <SkillLaunchContext.Provider value={skillLaunch}>
        <ComposerDraftContext.Provider value={composerDraftScope}>
          <MessageEditorContext.Provider value={{ editor, setEditor }}>
            <VideoGenerationProvider task={selectedAgent ? {
              threadId, agentName: selectedAgent.name, agentVersion: selectedAgent.version,
              agentOwnerUserId: selectedAgent.ownerUserId, spaceId: selectedAgent.spaceId,
            } : undefined}>
            <div className="harness-thread-frame" ref={frame}>
            <ConversationIndex frame={frame} threadId={threadId} pagination={historyPagination} />
            {historyPagination.loading ? (
              <div className="history-load-earlier" role="status" aria-live="polite">
                <span className="history-load-earlier-status">正在加载更早的消息…</span>
              </div>
            ) : null}
            <ConversationThread threadId={threadId}
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
              Composer: ComposerComponent,
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
