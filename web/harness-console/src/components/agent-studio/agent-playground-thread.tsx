"use client";
import { FeedbackToast } from "../feedback-toast";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  AssistantRuntimeProvider,
  ExportedMessageRepository,
  useExternalStoreRuntime,
  type AppendMessage,
  type AssistantRuntime,
  type ThreadMessageLike,
  type ThreadMessage,
  type CompleteAttachment,
} from "@assistant-ui/react";
import { AgentThread } from "../agent-thread";
import { TaskModelProvider } from "../task-model-context";
import { TaskKnowledgeProvider } from "../task-knowledge-context";
import {
  ConversationScopeProvider,
  type ConversationScope,
} from "../../lib/conversation-scope";
import {
  createInputAttachmentAdapter,
  inputArtifactIdFromAttachment,
} from "../../lib/input-attachment-adapter";
import { reduceRunViewModel } from "../../lib/run-view-model";
import { studioClient } from "../../lib/studio-client";
import type { StudioDraft } from "../../lib/agent-studio";
import type { PreviewTurn } from "./agent-preview";
import { projectTryRunConversation } from "./try-run-stream";
import { ApprovalBatch } from "./approval-batch";
import styles from "./build-workspace.module.css";
const terminalStatuses = new Set([
  "succeeded",
  "failed",
  "cancelled",
  "timed_out",
  "rejected",
]);
export function previewThreadMessages(
  turns: PreviewTurn[],
): ThreadMessageLike[] {
  return turns.flatMap((turn) => {
    const result = turn.result;
    const terminal = terminalStatuses.has(result.run.status);
    const projected = projectTryRunConversation(result.events);
    const answer = terminal
      ? result.finalText || projected.answerText
      : projected.answerText;
    const content: Exclude<
      NonNullable<ThreadMessageLike["content"]>,
      string
    >[number][] = [];
    if (result.activity)
      content.push({
        type: "tool-call",
        toolCallId: `activity-${result.run.run_id}`,
        toolName: "harness_run_activity",
        args: JSON.parse(JSON.stringify({ activity: result.activity })),
        result: { status: result.run.status },
      });
    else if (projected.processText)
      content.push({ type: "reasoning", text: projected.processText });
    for (const artifact of result.artifacts.filter(
      (item) => item.status === "ready",
    ))
      content.push({
        type: "tool-call",
        toolCallId: `artifact-${artifact.artifact_id}`,
        toolName: "harness_present_artifact",
        args: { ...artifact, run_id: result.run.run_id },
        result: { status: "ready" },
      });
    if (answer) content.push({ type: "text", text: answer });
    else if (terminal)
      content.push({
        type: "text",
        text:
          result.run.status === "succeeded"
            ? "本轮已结束，未返回文字。可查看交付文件和执行详情。"
            : `本轮未完成。${result.run.error_code || "请查看执行详情后重试。"}`,
      });
    return [
      {
        id: `user-${result.run.run_id}`,
        role: "user",
        content: [{ type: "text", text: turn.prompt }],
        attachments: (turn.artifactIds ?? []).map(
          (id, index) =>
            ({
              id,
              name: turn.files?.[index] || `附件 ${index + 1}`,
              type: /\.(png|jpg|jpeg|gif|webp|avif)$/i.test(
                turn.files?.[index] || "",
              )
                ? "image"
                : "document",
              content: [
                {
                  type: "file",
                  data: id,
                  mimeType: "application/octet-stream",
                  filename: turn.files?.[index],
                },
              ],
              status: { type: "complete" },
            }) as CompleteAttachment,
        ),
      },
      {
        id: `assistant-${result.run.run_id}`,
        role: "assistant",
        content,
        status: terminal
          ? result.run.status === "succeeded"
            ? { type: "complete", reason: "stop" }
            : {
                type: "incomplete",
                reason:
                  result.run.status === "cancelled" ? "cancelled" : "error",
              }
          : { type: "running" },
      },
    ] as ThreadMessageLike[];
  });
}
export function AgentPlaygroundThread({
  turns,
  messageOverride,
  afterLastMessage,
  composerAccessory,
  draft,
  agentName,
  model,
  draftId,
  scopeId,
  userId,
  ready,
  busy,
  loading,
  error,
  seed,
  selectedRunId,
  onSend,
  onRerun,
  onCancel,
  onReset,
  onAssets,
  onConfigureKnowledge,
  incomingFiles,
  onIncomingFilesUsed,
}: {
  turns: PreviewTurn[];
  messageOverride?: ThreadMessageLike[];
  afterLastMessage?: ReactNode;
  composerAccessory?: ReactNode;
  draft?: StudioDraft;
  agentName: string;
  model: string;
  draftId: string;
  scopeId: string;
  userId: string;
  ready: boolean;
  busy: boolean;
  loading: boolean;
  error: string;
  seed?: { key: number; text: string };
  selectedRunId: string;
  onSend: (value: string, ids: string[], names: string[]) => Promise<boolean>;
  onRerun?: (value: string, ids: string[], names: string[]) => Promise<boolean>;
  onCancel: () => Promise<void>;
  onReset: () => void;
  onAssets: () => void;
  onConfigureKnowledge: () => void;
  incomingFiles?: {
    draftId: string;
    files: { id: string; name: string }[];
  } | null;
  onIncomingFilesUsed?: () => void;
}) {
  const [localError, setLocalError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const submitLock = useRef(false);
  const runtimeRef = useRef<AssistantRuntime | null>(null);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);
  const attachments = useMemo(() => createInputAttachmentAdapter(), []);
  const messages = useMemo(() => messageOverride ?? previewThreadMessages(turns), [messageOverride, turns]);
  // This workspace owns a linear transcript. Removed progress placeholders and
  // messages from another session must not remain as alternate answer branches.
  const messageRepository = useMemo(() => ExportedMessageRepository.fromArray(messages), [messages]);
  async function submit(message: AppendMessage, rerun = false) {
    const text = message.content
      .filter((part) => part.type === "text")
      .map((part) => part.text)
      .join("\n");
    const files = message.attachments ?? [];
    const ids = files.map(inputArtifactIdFromAttachment);
    let accepted = false;
    try {
      if (submitLock.current || busy || !ready || loading)
        throw new Error("请等待当前操作完成后再发送。");
      if (ids.some((id) => !id)) throw new Error("附件尚未就绪，请重新添加。");
      submitLock.current = true;
      setSubmitting(true);
      setLocalError("");
      accepted = await (rerun && onRerun ? onRerun : onSend)(
        text || "请分析附加材料。",
        ids as string[],
        files.map((file) => file.name),
      );
    } catch (reason) {
      if (alive.current)
        setLocalError(
          reason instanceof Error ? reason.message : "发送失败，请重试。",
        );
    } finally {
      submitLock.current = false;
      if (alive.current) {
        setSubmitting(false);
        if (!accepted && runtimeRef.current) {
          const composer = runtimeRef.current.thread.composer;
          const current = composer.getState();
          composer.setText([text, current.text].filter(Boolean).join("\n\n"));
          for (const file of files)
            if (!current.attachments.some((item) => item.id === file.id))
              await composer.addAttachment(file);
        }
      }
    }
  }
  const runtime = useExternalStoreRuntime<ThreadMessage>({
    messageRepository,
    isRunning: busy || submitting,
    isDisabled: !ready,
    isSendDisabled: loading,
    onNew: (message) => submit(message),
    onEdit: (message) => submit(message, true),
    onCancel,
    onReload: async () => {
      const last = turns.at(-1);
      if (last)
        await (onRerun ?? onSend)(
          last.prompt,
          last.artifactIds ?? [],
          last.files ?? [],
        );
    },
    adapters: { attachments },
  });
  runtimeRef.current = runtime;
  useEffect(() => {
    if (seed) runtime.thread.composer.setText(seed.text);
  }, [runtime, seed]);
  useEffect(() => {
    if (!incomingFiles || incomingFiles.draftId !== draftId) return;
    for (const file of incomingFiles.files)
      void runtime.thread.composer.addAttachment({
        id: file.id,
        name: file.name,
        type: /\.(png|jpg|jpeg|gif|webp|avif)$/i.test(file.name)
          ? "image"
          : "document",
        content: [
          {
            type: "file",
            data: file.id,
            mimeType: "application/octet-stream",
            filename: file.name,
          },
        ],
      });
    onIncomingFilesUsed?.();
  }, [draftId, incomingFiles, onIncomingFilesUsed, runtime]);
  useEffect(() => {
    if (selectedRunId)
      document
        .querySelector(`[data-test-run="${CSS.escape(selectedRunId)}"]`)
        ?.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [selectedRunId]);
  const latest = turns.at(-1)?.result;
  const currentRun = messages.at(-1)?.id === `assistant-${latest?.run.run_id}` ? latest : undefined;
  const activity = currentRun?.activity ?? undefined;
  const pending =
    currentRun?.approvals.filter((item) => item.status === "pending") ?? [];
  const scope: ConversationScope = useMemo(() => ({
    compactComposer: true,
    composerAccessory,
    composerPlaceholder: "输入任务，或告诉我如何调整智能体…",
    activity,
    view: activity ? reduceRunViewModel(undefined, activity) : undefined,
    stream: { status: busy ? "running" : "idle", runId: currentRun?.run.run_id },
    live: { text: "", status: "idle", visible: false },
    approval:
      pending.length === 1
        ? {
            visible: true,
            details: {
              ...pending[0],
              run_id: latest!.run.run_id,
              tool_name: pending[0].tool_name ?? undefined,
              risk: pending[0].risk ?? undefined,
            },
          }
        : { visible: false },
    onOpenFiles: onAssets,
    onNew: onReset,
    onConfigureKnowledge,
    onApproval: async (id, decision) => {
      await studioClient.decideTryRunApproval(id, decision);
    },
    afterMessage: (id) => {
      const turn = turns.find(
        (item) => `assistant-${item.result.run.run_id}` === id,
      );
      const extra = id === messages.at(-1)?.id ? afterLastMessage : null;
      if (!turn) return extra;
      const approvals =
        turn.result.run.run_id === latest?.run.run_id && pending.length > 1 ? (
          <ApprovalBatch approvals={pending} />
        ) : null;
      // An empty wrapper still carries the 12px margins from the stylesheet and
      // reads as a gap between the process log and the answer.
      if (!extra && !approvals) return null;
      return (
        <div className={styles.turnExtensions}>
          {extra}
          {approvals}
        </div>
      );
    },
  }), [activity, busy, currentRun, messages, pending, afterLastMessage, composerAccessory, onAssets, onReset, onConfigureKnowledge]);

  const selectedAgent = {
    name: draft?.name ?? draftId,
    displayName: agentName,
    version: draft?.version ?? "draft",
    domain: draft?.domain ?? "general",
    model,
    skills: draft?.skills,
  };
  return (
    <div className={styles.sharedThread} data-playground-conversation>
      <FeedbackToast message={error || localError} tone="error" />
      <ConversationScopeProvider value={scope}>
        <TaskKnowledgeProvider
          selected={draft?.knowledgeReferences ?? []}
          onChange={() => {}}
          mode="rag"
          onModeChange={() => {}}
        >
          <TaskModelProvider
            routes={[
              {
                id: draft?.modelRoute ?? "draft",
                label: model || "智能体默认模型",
                model,
                provider: "configured",
                modelType: "chat",
                capabilities: draft?.requiredCapabilities ?? [],
              },
            ]}
            agentDefaultRouteId={draft?.modelRoute ?? "draft"}
            overrideRouteId={null}
            onOverrideChange={() => {}}
          >
              <AssistantRuntimeProvider runtime={runtime}>
                <AgentThread
                  userId={userId}
                  threadId={scopeId}
                  agents={[selectedAgent]}
                  selectedAgent={selectedAgent}
                  currentTaskBusy
                />
              </AssistantRuntimeProvider>
          </TaskModelProvider>
        </TaskKnowledgeProvider>
      </ConversationScopeProvider>

    </div>
  );
}
