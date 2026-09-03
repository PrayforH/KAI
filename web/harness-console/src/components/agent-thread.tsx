"use client";

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
import { ActivitySummary } from "./activity-summary";
import { TaskAgentSwitcher } from "./task-agent-switcher";
import { ApprovalCard, type ApprovalDetails } from "./approval-card";
import { ArtifactCard, type ArtifactDetails } from "./artifact-list";
import { MarkdownText } from "./markdown-text";
import { SubagentCard } from "./subagent-card";
import { ToolCard } from "./tool-card";
import { useRunActivity, useRunViewModel } from "../lib/activity-store";
import { selectComposerDisabled, type RunPhase } from "../lib/run-view-model";
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
  return threadRunning || streamStatus === "running";
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
  const reuseNotice = useRunReuseNotice();
  const runLocked = selectComposerDisabled(runView);
  useTaskComposerDraft(composerText);
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
      <Composer.Root>
        <Composer.Attachments />
        <Composer.Input
          autoFocus
          onKeyDown={(event: ReactKeyboardEvent<HTMLTextAreaElement>) => {
            if (
              videoRoute &&
              event.key === "Enter" &&
              !event.shiftKey &&
              !event.nativeEvent.isComposing
            ) {
              event.preventDefault();
              void generateVideo();
            }
          }}
        />
        <div className="composer-toolbar">
          <Composer.AddAttachment>
            <svg className="aui-composer-attach-icon" viewBox="0 0 20 20" aria-hidden="true">
              <path d="M10 4.5v11M4.5 10h11" />
            </svg>
          </Composer.AddAttachment>
          <TaskAgentSwitcher
            agents={agentSelection.agents}
            selected={agentSelection.selected}
            loading={agentSelection.loading}
            currentTaskBusy={agentSelection.currentTaskBusy}
            onChange={agentSelection.onChange}
          />
          <TaskModelControl disabled={runLocked || showStop || videoGenerating} />
        </div>
        {showStop ? (
          <button
            type="button"
            className="aui-button aui-button-icon aui-composer-cancel"
            aria-label="停止运行"
            onClick={() => aui.thread().cancelRun()}
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
          <Composer.Send>
            <svg className="aui-composer-send-icon" viewBox="0 0 20 20" aria-hidden="true">
              <path d="M10 16.5v-11M5.5 9.5 10 5l4.5 4.5" />
            </svg>
          </Composer.Send>
        )}
      </Composer.Root>
    </div>
  );
}

type ComposerDraftScope = {
  userId: string;
  threadId: string;
};

const ComposerDraftContext = createContext<ComposerDraftScope | null>(null);

type AgentSelectionContextValue = {
  agents: readonly TaskAgent[];
  selected: TaskAgent | null;
  loading: boolean;
  currentTaskBusy: boolean;
  onChange: (agent: TaskAgent) => void;
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
          <h1>把目标交给智能体，让它替你完成</h1>
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
  <details className="reasoning-card" open={status.type === "running"}>
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
  return (
    <AssistantMessage.Root
      className="harness-assistant-message"
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
          </AssistantActionBar.Root>
        </div>
      ) : null}
    </AssistantMessage.Root>
  );
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

function AttachmentFileIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M5.5 2.8h5.8l3.2 3.3v11.1H5.5Z" />
      <path d="M11.2 2.8v3.5h3.3" />
      <path d="M7.8 10h4.4M7.8 13h4.4" />
    </svg>
  );
}

export function inputArtifactDownloadHref(
  data: string | undefined,
  attachmentId: string,
) {
  const artifactId = data?.startsWith("input_artifact_")
    ? data
    : attachmentId.startsWith("input_artifact_")
      ? attachmentId
      : undefined;
  return artifactId
    ? `/api/input-artifacts/${encodeURIComponent(artifactId)}/content`
    : undefined;
}

function HarnessMessageAttachment() {
  const attachment = useAttachment((state) => state);
  const [previewOpen, setPreviewOpen] = useState(false);
  const filePart = attachment.content?.find((part) => part.type === "file");
  const imagePart = attachment.content?.find((part) => part.type === "image");
  const data = filePart?.type === "file"
    ? filePart.data
    : imagePart?.type === "image"
      ? imagePart.image
      : undefined;
  const href = inputArtifactDownloadHref(data, attachment.id);
  const extension = attachment.name.split(".").at(-1)?.toUpperCase() || "文件";
  const contentType = attachment.contentType
    ?? (filePart?.type === "file" ? filePart.mimeType : undefined);
  const isImage =
    attachment.type === "image"
    || contentType?.startsWith("image/")
    || ["AVIF", "GIF", "HEIC", "HEIF", "JPEG", "JPG", "PNG", "WEBP"].includes(
      extension,
    );
  const imageSrc = isImage
    ? href ?? (data?.startsWith("data:") || data?.startsWith("http") ? data : undefined)
    : undefined;
  const content = (
    <>
      {imageSrc ? (
        <span className="message-attachment-preview">
          {/* The same-origin artifact endpoint enforces the current user scope. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={imageSrc} alt={`${attachment.name} 缩略图`} />
        </span>
      ) : (
        <span className="message-attachment-icon"><AttachmentFileIcon /></span>
      )}
      <span className="message-attachment-copy">
        <strong>{attachment.name}</strong>
        <small>
          {extension} {isImage ? "图片" : "文件"}
          {href ? (isImage ? " · 点击查看" : " · 点击下载") : ""}
        </small>
      </span>
    </>
  );
  useEffect(() => {
    if (!previewOpen) return;
    const previousOverflow = document.body.style.overflow;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPreviewOpen(false);
    };
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [previewOpen]);

  const preview = previewOpen && imageSrc
    ? createPortal(
        <div
          className="image-lightbox"
          role="dialog"
          aria-modal="true"
          aria-label={`${attachment.name} 原图预览`}
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setPreviewOpen(false);
          }}
        >
          <header className="image-lightbox-toolbar">
            <span className="image-lightbox-title">
              <small>上传原图</small>
              <strong>{attachment.name}</strong>
            </span>
            <span className="image-lightbox-actions">
              {href ? (
                <a href={href} download={attachment.name}>
                  下载原图
                </a>
              ) : null}
              <button
                type="button"
                onClick={() => setPreviewOpen(false)}
                aria-label="关闭原图预览"
                autoFocus
              >
                ×
              </button>
            </span>
          </header>
          <div className="image-lightbox-stage">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={imageSrc} alt={attachment.name} />
          </div>
        </div>,
        document.body,
      )
    : null;
  return (
    <>
      <AttachmentPrimitive.Root
        className="message-attachment-card"
        data-kind={isImage ? "image" : "file"}
      >
        {isImage && imageSrc ? (
          <button
            className="message-attachment-open"
            type="button"
            onClick={() => setPreviewOpen(true)}
            title={`放大查看 ${attachment.name}`}
          >
            {content}
          </button>
        ) : href ? (
          <a
            href={href}
            download={attachment.name}
            title={`下载 ${attachment.name}`}
          >
            {content}
          </a>
        ) : (
          <span className="message-attachment-static">{content}</span>
        )}
      </AttachmentPrimitive.Root>
      {preview}
    </>
  );
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
      <UserMessage.Root className="harness-user-message">
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
  onAgentChange = () => undefined,
}: {
  userId: string;
  threadId: string;
  agents?: readonly TaskAgent[];
  selectedAgent?: TaskAgent | null;
  agentsLoading?: boolean;
  currentTaskBusy?: boolean;
  onAgentChange?: (agent: TaskAgent) => void;
}) {
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
    }),
    [agents, agentsLoading, currentTaskBusy, onAgentChange, selectedAgent],
  );
  return (
    <AgentSelectionContext.Provider value={agentSelection}>
      <ComposerDraftContext.Provider value={composerDraftScope}>
        <MessageEditorContext.Provider value={{ editor, setEditor }}>
          <VideoGenerationProvider>
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
          </VideoGenerationProvider>
        </MessageEditorContext.Provider>
      </ComposerDraftContext.Provider>
    </AgentSelectionContext.Provider>
  );
}
