import {
  type ActivityItem,
  type RunActivity,
  runActivitySchema,
} from "./activity-schema";
import { isResponseBoundary, isStableReasoningBlockId } from "./process-boundary";

/**
 * Session-level trace model. A 会话 (thread) holds many runs; each run is one
 * 轮次 (turn). The AG-UI history endpoint embeds each run's durable activity
 * payload as a `harness_run_activity` tool call on the assistant message, so
 * the trace console is a pure client projection — no new backend surface.
 */

export interface SessionTraceRun {
  runId: string;
  turn: number;
  prompt?: string;
  activity?: RunActivity;
}

export type TraceLane = "input" | "model" | "tool";

/** System-prompt summary resolved from the agent's versioned draft. */
export interface TraceManifest {
  systemPrompt?: string;
  /** Reasoning text absorbed from the thinking spans preceding this answer. */
  thinking?: string;
  entries: Array<{ name: string; description: string }>;
}

export interface TraceNode {
  id: string;
  runId: string;
  turn: number;
  /** Step index within the turn, for the detail header ("步骤 21"). */
  step: number;
  lane: TraceLane;
  badge: string;
  label: string;
  detail: string;
  status: string;
  startMs: number;
  endMs: number;
  argumentsText?: string;
  output?: string;
  summary?: string;
  systemPrompt?: string;
  /** Reasoning text absorbed from the thinking spans preceding this answer. */
  thinking?: string;
  entries?: Array<{ name: string; description: string }>;
  citations?: Array<{
    index: number;
    title?: string;
    sourceReference: string;
    uri?: string;
    score?: number;
  }>;
  artifact?: {
    id: string;
    name: string;
    mediaType?: string;
    sizeBytes?: number;
  };
  running: boolean;
}

export interface TurnWindow {
  turn: number;
  runId: string;
  /** Active span of the run (absolute clock). */
  startMs: number;
  endMs: number;
  /** Packed layout position (%) on the compressed axis. */
  left: number;
  width: number;
}

export interface SessionTrace {
  runs: SessionTraceRun[];
  nodes: TraceNode[];
  window: { startMs: number; endMs: number; totalMs: number };
  turns: TurnWindow[];
  summary: { turns: number; toolCalls: number; durationMs: number };
  /** Run-level model usage aggregated from runtime.result events. */
  usage: { inputTokens: number; outputTokens: number; known: boolean };
}

interface HistoryToolCall {
  id?: string;
  function?: { name?: string; arguments?: string };
}

interface HistoryMessageLike {
  id?: string;
  role?: string;
  content?: unknown;
  toolCalls?: unknown[];
  tool_calls?: unknown[];
}

const RUN_ID_SUFFIX = /^(?:user|assistant)-(.+)$/;
const PREVIEW_LIMIT = 160;

function messageToolCalls(message: HistoryMessageLike): HistoryToolCall[] {
  const raw = Array.isArray(message.toolCalls)
    ? message.toolCalls
    : Array.isArray(message.tool_calls)
      ? message.tool_calls
      : [];
  return raw.filter(
    (call): call is HistoryToolCall =>
      Boolean(call) && typeof call === "object",
  );
}

function historyPromptText(content: unknown): string | undefined {
  if (typeof content === "string" && content.trim()) return content;
  if (!Array.isArray(content)) return undefined;
  const parts = content
    .map((part) => {
      const candidate = part as { type?: string; text?: string };
      return candidate?.type === "text" && typeof candidate.text === "string"
        ? candidate.text
        : "";
    })
    .filter(Boolean);
  return parts.length ? parts.join("\n") : undefined;
}

function activityFromToolCall(call: HistoryToolCall): RunActivity | undefined {
  if (call.function?.name !== "harness_run_activity") return undefined;
  const raw = call.function.arguments;
  if (typeof raw !== "string") return undefined;
  try {
    const payload = JSON.parse(raw) as { activity?: unknown };
    const parsed = runActivitySchema.safeParse(payload.activity);
    return parsed.success ? parsed.data : undefined;
  } catch {
    return undefined;
  }
}

/** Extract ordered per-run activities and prompts from accumulated history. */
export function extractSessionRuns(
  messages: readonly unknown[],
): SessionTraceRun[] {
  const prompts = new Map<string, string>();
  const runs: SessionTraceRun[] = [];
  const seen = new Set<string>();

  for (const raw of messages) {
    const message = raw as HistoryMessageLike;
    if (typeof message?.id !== "string") continue;
    const idMatch = RUN_ID_SUFFIX.exec(message.id);
    const runId = idMatch?.[1];
    if (!runId) continue;

    if (message.id.startsWith("user-") && !prompts.has(runId)) {
      const prompt = historyPromptText(message.content);
      if (prompt) prompts.set(runId, prompt);
      continue;
    }
    if (!message.id.startsWith("assistant-") || seen.has(runId)) continue;
    const activity = messageToolCalls(message)
      .map(activityFromToolCall)
      .find(Boolean);
    if (!activity) continue;
    seen.add(runId);
    runs.push({
      runId,
      turn: runs.length + 1,
      prompt: prompts.get(runId),
      activity,
    });
  }
  return runs;
}

function safeMs(value: string | undefined): number {
  if (!value) return Number.NaN;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : Number.NaN;
}

function preview(value: string, limit = PREVIEW_LIMIT): string {
  const flat = value.replace(/\s+/g, " ").trim();
  return flat.length > limit ? `${flat.slice(0, limit)}…` : flat;
}

function toolArgumentsText(argumentsValue: unknown): string {
  if (!argumentsValue || typeof argumentsValue !== "object") return "";
  return JSON.stringify(argumentsValue, null, 2);
}

function toolInputPreview(
  name: string,
  argumentsValue: Record<string, unknown>,
): string {
  if (name === "Bash" && typeof argumentsValue.command === "string") {
    return preview(argumentsValue.command, 120);
  }
  return preview(JSON.stringify(argumentsValue), 120);
}

function toolLabel(argumentsValue: Record<string, unknown>): string {
  const description = argumentsValue.description;
  if (typeof description === "string" && description.trim()) {
    return preview(description, 80);
  }
  return "";
}

export function formatDuration(ms: number): string {
  if (!Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
}

export function formatClock(ms: number): string {
  if (!Number.isFinite(ms)) return "—";
  const date = new Date(ms);
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
}

const TERMINAL_STATUSES = new Set(["succeeded", "completed", "passed"]);
const RUNNING_STATUSES = new Set(["running", "queued", "provisioning", "waiting"]);
// Environment/context framing events: they describe what surrounded the model
// call (assets, permissions, runtime, workspace) rather than an action.
const CONTEXT_EVENT_TYPES = new Set([
  "context.compaction.started",
  "context.compaction.completed",
  "context.compacted",
  "context.compaction.failed",
  "agent.assets.staged",
  "policy.resolved",
  "runtime.system",
  "workspace.restored",
  "workspace.archived",
]);

function isRunningStatus(status: string): boolean {
  return RUNNING_STATUSES.has(status);
}

interface MessageSpan {
  startSequence: number;
  startMs: number;
  endMs: number;
  output: string;
  status: string;
  thinking: boolean;
}

function buildTraceNodes(
  runs: readonly SessionTraceRun[],
  manifest?: TraceManifest,
): TraceNode[] {
  const nodes: TraceNode[] = [];

  for (const run of runs) {
    const activity = run.activity;
    if (!activity) continue;
    const startedMs = safeMs(activity.started_at);
    const spans = new Map<string, MessageSpan>();
    const openStreams = new Map<string, string>();
    let segment = 0;
    let reasoningSegment = 0;
    const results = new Map<string, ActivityItem>();
    const approvals = new Map<string, ActivityItem>();
    const contextFacts: Array<{
      id: string;
      title: string;
      summary?: string;
      timestampMs: number;
      entries?: Array<{ name: string; description: string }>;
    }> = [];
    const approvalTerminals = new Map<string, ActivityItem>();
    const subagentTerminals = new Map<string, ActivityItem>();

    for (const item of activity.items) {
      const toolCallId = item.metadata.tool_call_id;
      if (
        typeof toolCallId === "string" &&
        (item.event_type === "tool.result" || item.event_type === "tool.allowed")
      ) {
        results.set(toolCallId, item);
      }
      // approval.requested carries tool_call_id; terminal approval events carry
      // only approval_id, so pair them in two steps.
      if (item.event_type === "approval.requested" && typeof toolCallId === "string") {
        approvals.set(toolCallId, item);
      }
      const approvalId = item.metadata.approval_id;
      if (
        typeof approvalId === "string" &&
        ["approval.approved", "approval.rejected", "approval.expired", "approval.cancelled"]
          .includes(item.event_type)
      ) {
        approvalTerminals.set(approvalId, item);
      }
      const taskId = item.metadata.task_id;
      if (
        item.kind === "subagent" &&
        typeof taskId === "string" &&
        ["subagent.completed", "subagent.failed", "subagent.cancelled"].includes(item.event_type)
      ) {
        subagentTerminals.set(taskId, item);
      }
    }

    for (const item of activity.items) {
      const timestampMs = safeMs(item.timestamp);
      if (!Number.isFinite(timestampMs)) continue;
      if (isResponseBoundary(item.event_type)) {
        segment += 1;
        reasoningSegment += 1;
        openStreams.clear();
      }
      if (item.event_type === "message.start") openStreams.delete("answer");

      if (CONTEXT_EVENT_TYPES.has(item.event_type)) {
        // `runtime.system` includes heartbeat-like "模型正在处理" frames.
        // They are status narration, not user-visible steps.
        if (
          item.event_type === "runtime.system" &&
          item.title !== "运行时与工具已连接" &&
          item.title !== "正在压缩长上下文"
        ) {
          continue;
        }
        contextFacts.push({
          id: item.id,
          title: item.title || item.event_type,
          summary: item.summary ?? undefined,
          timestampMs,
          entries: item.event_type === "agent.assets.staged" ? manifest?.entries : undefined,
        });
        continue;
      }

      const isMessageFrame =
        item.event_type === "message.delta" || item.event_type === "message.completed";
      const isReasoningFrame = item.event_type.startsWith("reasoning.");
      if (isMessageFrame || isReasoningFrame) {
        // Reasoning streams carry item_id; answer streams carry message_id.
        // Keying reasoning by message_id (absent) used to split every delta
        // into its own 思考 row.
        const rawId = isReasoningFrame
          ? item.metadata.item_id ?? item.metadata.message_id
          : item.metadata.message_id;
        const channel = isReasoningFrame
          ? item.event_type.startsWith("reasoning.summary.") ? "reasoning-summary" : "reasoning"
          : "answer";
        // Some runtimes (including DeepAgents) omit stream IDs. Their event
        // IDs identify individual tokens, not messages. Keep one fallback
        // stream until a real message/tool boundary, including history replay.
        // SDK blocks remain the same block when tool callbacks overtake a
        // buffered delta. Reused/unlabelled provider IDs still need boundaries.
        const stableBlock = isReasoningFrame && typeof rawId === "string" &&
          isStableReasoningBlockId(run.runId, rawId);
        const streamSegment = stableBlock ? "block" : isReasoningFrame ? reasoningSegment : segment;
        const messageId = typeof rawId === "string" && rawId
          ? `${channel}:${streamSegment}:${rawId}`
          : openStreams.get(channel) ?? `${channel}:${streamSegment}:${item.id}`;
        openStreams.set(channel, messageId);
        if (isMessageFrame) {
          reasoningSegment += 1;
          openStreams.delete("reasoning");
          openStreams.delete("reasoning-summary");
        }
        const existing = spans.get(messageId);
        const text = typeof item.summary === "string" ? item.summary : "";
        const completed = isMessageFrame
          ? "message.completed"
          : "reasoning.completed";
        if (existing) {
          existing.endMs = Math.max(existing.endMs, timestampMs);
          if (item.event_type === completed && text) existing.output = text;
          else if (text) existing.output += text;
          if (TERMINAL_STATUSES.has(item.status)) existing.status = item.status;
        } else {
          spans.set(messageId, {
            startSequence: item.sequence,
            startMs: timestampMs,
            endMs: timestampMs,
            output: text,
            status: item.status,
            thinking: isReasoningFrame,
          });
        }
        if (item.event_type === completed) openStreams.delete(channel);
        continue;
      }

      if (item.event_type === "tool.request") {
        const name = typeof item.metadata.name === "string"
          ? item.metadata.name
          : "tool";
        const rawArguments = item.metadata.arguments;
        const argumentsValue =
          rawArguments && typeof rawArguments === "object" && !Array.isArray(rawArguments)
            ? rawArguments as Record<string, unknown>
            : {};
        const toolCallId = typeof item.metadata.tool_call_id === "string"
          ? item.metadata.tool_call_id
          : item.id;
        const result = results.get(toolCallId);
        const requested = approvals.get(toolCallId);
        const approvalId = typeof requested?.metadata.approval_id === "string"
          ? requested.metadata.approval_id
          : undefined;
        const approvalEnd = approvalId ? approvalTerminals.get(approvalId) : undefined;
        const endItem = result ?? approvalEnd;
        const endMs = endItem ? safeMs(endItem.timestamp) : Number.NaN;
        const waitingApproval = Boolean(requested) && !approvalEnd && !result;
        const rawCitations = result?.metadata.citations;
        const citations = Array.isArray(rawCitations)
          ? rawCitations
              .map((entry) => entry as Record<string, unknown>)
              .filter((entry) => typeof entry.sourceReference === "string")
              .map((entry) => ({
                index: typeof entry.index === "number" ? entry.index : 0,
                title: typeof entry.title === "string" ? entry.title : undefined,
                sourceReference: entry.sourceReference as string,
                uri: typeof entry.uri === "string" ? entry.uri : undefined,
                score: typeof entry.score === "number" ? entry.score : undefined,
              }))
          : undefined;
        nodes.push({
          id: `tool-${run.runId}-${toolCallId}`,
          runId: run.runId,
          turn: run.turn,
          step: nodes.filter((node) => node.runId === run.runId).length + 1,
          lane: "tool",
          badge: "工具",
          label: name,
          detail: `${waitingApproval ? "待审批 · " : ""}${toolInputPreview(name, argumentsValue)}${
            result?.metadata.result_preview
              ? ` → ${preview(String(result.metadata.result_preview), 90)}`
              : result?.metadata.result_summary
                ? ` → ${preview(String(result.metadata.result_summary), 90)}`
                : ""
          }`,
          status: result?.status ?? approvalEnd?.status ?? (waitingApproval ? "waiting" : item.status),
          startMs: timestampMs,
          endMs: Number.isFinite(endMs) ? endMs : timestampMs,
          argumentsText: toolArgumentsText(argumentsValue),
          output: typeof result?.metadata.result_preview === "string"
            ? result.metadata.result_preview
            : typeof result?.metadata.result_summary === "string"
              ? result.metadata.result_summary
              : undefined,
          summary: toolLabel(argumentsValue) || item.summary || undefined,
          citations: citations?.length ? citations : undefined,
          running: !endItem && (waitingApproval || isRunningStatus(item.status)),
        });
        continue;
      }

      // Approval nodes stand alone only when no tool call carries them; a
      // paired approval is already reflected on its tool node's status.
      if (item.event_type === "approval.requested") {
        const toolCallId = item.metadata.tool_call_id;
        const pairedToolRequest =
          typeof toolCallId === "string" &&
          activity.items.some(
            (candidate) =>
              candidate.event_type === "tool.request" &&
              candidate.metadata.tool_call_id === toolCallId,
          );
        if (pairedToolRequest) continue;
        const approvalId = typeof item.metadata.approval_id === "string"
          ? item.metadata.approval_id
          : undefined;
        const approvalEnd = approvalId ? approvalTerminals.get(approvalId) : undefined;
        const endMs = approvalEnd ? safeMs(approvalEnd.timestamp) : Number.NaN;
        nodes.push({
          id: `approval-${run.runId}-${item.id}`,
          runId: run.runId,
          turn: run.turn,
          step: nodes.filter((node) => node.runId === run.runId).length + 1,
          lane: "tool",
          badge: "审批",
          label: item.title || "等待人工审批",
          detail: preview(item.summary ?? "", 120),
          status: approvalEnd?.status ?? item.status,
          startMs: timestampMs,
          endMs: Number.isFinite(endMs) ? endMs : timestampMs,
          running: !approvalEnd,
        });
        continue;
      }

      if (
        typeof item.metadata.approval_id === "string" &&
        ["approval.approved", "approval.rejected", "approval.expired", "approval.cancelled"]
          .includes(item.event_type)
      ) {
        // Terminal approval events are folded into their request's node above.
        continue;
      }

      if (item.kind === "subagent") {
        if (item.event_type !== "subagent.started") continue;
        const taskId = typeof item.metadata.task_id === "string"
          ? item.metadata.task_id
          : item.id;
        const terminal = subagentTerminals.get(taskId);
        const endMs = terminal ? safeMs(terminal.timestamp) : Number.NaN;
        nodes.push({
          id: `subagent-${run.runId}-${taskId}`,
          runId: run.runId,
          turn: run.turn,
          step: nodes.filter((node) => node.runId === run.runId).length + 1,
          lane: "tool",
          badge: "子任务",
          label: item.title || "子任务",
          detail: preview(item.summary ?? terminal?.summary ?? "", 120),
          status: terminal?.status ?? item.status,
          startMs: timestampMs,
          endMs: Number.isFinite(endMs) ? endMs : timestampMs,
          summary: item.summary ?? undefined,
          running: !terminal,
        });
        continue;
      }

      if (item.event_type === "artifact.ready" || item.kind === "artifact") {
        const artifactId = item.metadata.artifact_id;
        nodes.push({
          id: `artifact-${run.runId}-${item.id}`,
          runId: run.runId,
          turn: run.turn,
          step: nodes.filter((node) => node.runId === run.runId).length + 1,
          lane: "tool",
          badge: "产物",
          label: typeof item.metadata.source_path === "string"
            ? item.metadata.source_path
            : item.title,
          detail: preview(item.summary ?? "", 120),
          status: item.status,
          startMs: timestampMs,
          endMs: timestampMs,
          summary: item.summary ?? undefined,
          artifact: typeof artifactId === "string"
            ? {
                id: artifactId,
                name: typeof item.metadata.source_path === "string"
                  ? item.metadata.source_path
                  : item.summary ?? "未命名产物",
                mediaType: typeof item.metadata.media_type === "string"
                  ? item.metadata.media_type
                  : undefined,
                sizeBytes: typeof item.metadata.size_bytes === "number"
                  ? item.metadata.size_bytes
                  : undefined,
              }
            : undefined,
          running: isRunningStatus(item.status),
        });
        continue;
      }

      if (item.kind === "error") {
        nodes.push({
          id: `error-${run.runId}-${item.id}`,
          runId: run.runId,
          turn: run.turn,
          step: nodes.filter((node) => node.runId === run.runId).length + 1,
          lane: "tool",
          badge: "异常",
          label: item.title || "运行异常",
          detail: preview(item.summary ?? "", 120),
          status: item.status,
          startMs: timestampMs,
          endMs: timestampMs,
          output: item.summary ?? undefined,
          running: false,
        });
      }
    }

    const spanEntries = [...spans.entries()].sort(
      ([, a], [, b]) => a.startMs - b.startMs,
    );
    // DSH nesting: thinking is folded into the assistant message it precedes
    // (the timeline block then covers thinking + answer). Thinking with no
    // following answer in the run stays a standalone row. Tools separate
    // provider streams, but must not prevent nesting in the next assistant.
    // Use event order, not a clock tolerance that can select a previous answer.
    const answers = spanEntries.filter(([, span]) => !span.thinking)
      .sort(([, a], [, b]) => a.startSequence - b.startSequence);
    const thinkingSpans = spanEntries.filter(([, span]) => span.thinking);
    const absorbedBy = new Map<string, string>();
    const thinkingByAnswer = new Map<string, string>();
    for (const [thinkingId, span] of thinkingSpans) {
      const nextAnswer = answers.find(([, answer]) =>
        answer.startSequence >= span.startSequence);
      if (nextAnswer) {
        absorbedBy.set(thinkingId, nextAnswer[0]);
        thinkingByAnswer.set(
          nextAnswer[0],
          [thinkingByAnswer.get(nextAnswer[0]), span.output].filter(Boolean).join("\n\n"),
        );
      }
    }
    for (const [messageId, span] of spanEntries) {
      const thinkingText = thinkingByAnswer.get(messageId);
      if (span.thinking && absorbedBy.has(messageId)) continue;
      const absorbedThinking = thinkingSpans
        .filter(([thinkingId]) => absorbedBy.get(thinkingId) === messageId)
        .map(([, thinkingSpan]) => thinkingSpan);
      nodes.push({
        id: `message-${run.runId}-${messageId}`,
        runId: run.runId,
        turn: run.turn,
        step: nodes.filter((node) => node.runId === run.runId).length + 1,
        lane: "model",
        badge: span.thinking ? "思考" : "助手",
        label: span.thinking ? "思考" : "助手",
        detail: preview(span.output, 140),
        status: span.status,
        startMs: absorbedThinking.length
          ? Math.min(span.startMs, ...absorbedThinking.map(thinking => thinking.startMs))
          : span.startMs,
        endMs: Math.max(span.endMs, ...absorbedThinking.map(thinking => thinking.endMs)),
        output: span.output || undefined,
        thinking: thinkingText || (span.thinking ? span.output : undefined),
        running: isRunningStatus(span.status),
      });
    }

    if (contextFacts.length) {
      const facts = [...contextFacts].sort((a, b) => a.timestampMs - b.timestampMs);
      const factText = facts
        .map((fact) => `${fact.title}${fact.summary ? `：${fact.summary}` : ""}`)
        .join("\n");
      const skills = facts.find((fact) => fact.entries)?.entries;
      nodes.push({
        id: `context-${run.runId}`,
        runId: run.runId,
        turn: run.turn,
        step: 0,
        lane: "input",
        badge: "上下文",
        label: "运行上下文",
        detail: preview(
          facts.map((fact) => fact.summary ?? fact.title).join(" · "),
          140,
        ),
        status: "succeeded",
        startMs: facts[0].timestampMs,
        endMs: facts.at(-1)?.timestampMs ?? facts[0].timestampMs,
        output: factText,
        entries: skills,
        running: false,
      });
    }

    if (run.prompt) {
      const firstItemMs = activity.items
        .map((item) => safeMs(item.timestamp))
        .find((value) => Number.isFinite(value));
      const started = Number.isFinite(startedMs) ? startedMs : firstItemMs;
      const inputStart = started ?? 0;
      const inputEnd =
        started !== undefined && firstItemMs !== undefined
          ? Math.max(started, firstItemMs)
          : inputStart;
      nodes.push({
        id: `input-${run.runId}`,
        runId: run.runId,
        turn: run.turn,
        step: 1,
        lane: "input",
        badge: "用户",
        label: preview(run.prompt, 80),
        detail: preview(run.prompt, 200),
        status: "succeeded",
        startMs: inputStart,
        endMs: inputEnd,
        output: run.prompt,
        running: false,
      });
    }
  }

  return nodes.sort((left, right) =>
    left.startMs - right.startMs || left.turn - right.turn || left.step - right.step,
  );
}

/** Merge the live run (if any) into extracted history runs, renumbering turns. */
export function mergeLiveRun(
  runs: readonly SessionTraceRun[],
  liveActivity: RunActivity | undefined,
): SessionTraceRun[] {
  if (!liveActivity?.run_id) return [...runs];
  const existing = runs.find((run) => run.runId === liveActivity.run_id);
  if (existing) {
    return runs.map((run) =>
      run.runId === liveActivity.run_id ? { ...run, activity: liveActivity } : run,
    );
  }
  return [...runs, { runId: liveActivity.run_id, turn: runs.length + 1, activity: liveActivity }];
}

export function buildSessionTrace(
  historyRuns: readonly SessionTraceRun[],
  liveActivity?: RunActivity,
  manifest?: TraceManifest,
): SessionTrace {
  const runs = mergeLiveRun(historyRuns, liveActivity);
  const nodes = buildTraceNodes(runs, manifest);

  // One 系统 row at session start: the versioned system prompt when the
  // draft is resolvable, otherwise the runtime facts for turn one.
  const firstRun = runs[0];
  const firstNode = nodes[0];
  if (firstRun && firstNode) {
    const route = [...(firstRun.activity?.items ?? [])]
      .reverse()
      .find((item) => item.event_type === "model.route.selected");
    const routeModel = typeof route?.metadata.model === "string"
      ? route.metadata.model
      : route?.summary ?? undefined;
    const startMs = firstNode.startMs;
    nodes.unshift({
      id: `system-${firstRun.runId}`,
      runId: firstRun.runId,
      turn: firstRun.turn,
      step: 0,
      lane: "input",
      badge: "系统",
      label: manifest?.systemPrompt ? "初始系统提示词" : "系统与运行时",
      detail: manifest?.systemPrompt
        ? preview(manifest.systemPrompt, 120)
        : `模型路由 ${routeModel ?? "—"} · 权限与运行时随轮次上下文注入`,
      status: "succeeded",
      startMs,
      endMs: startMs,
      systemPrompt: manifest?.systemPrompt,
      entries: manifest?.entries,
      summary: manifest?.systemPrompt ? undefined : routeModel,
      running: false,
    });
  }

  nodes.sort((left, right) =>
    left.startMs - right.startMs || left.turn - right.turn || left.step - right.step,
  );

  const nodeStarts = nodes.map((node) => node.startMs).filter(Number.isFinite);
  const nodeEnds = nodes.map((node) => node.endMs).filter(Number.isFinite);
  const lifecycleFor = (run: SessionTraceRun) => {
    const items = run.activity?.items ?? [];
    const scopedNodeStarts = nodes
      .filter((node) => node.runId === run.runId)
      .map((node) => node.startMs)
      .filter(Number.isFinite);
    const start = items
      .filter((item) => ["run.queued", "run.started", "run.running", "run.provisioning"].includes(item.event_type))
      .map((item) => safeMs(item.timestamp))
      .find(Number.isFinite);
    const terminal = items
      .filter((item) => item.event_type.startsWith("run.") && ["run.succeeded", "run.failed", "run.cancelled", "run.timed_out", "run.rejected"].includes(item.event_type))
      .map((item) => safeMs(item.timestamp))
      .find(Number.isFinite);
    return {
      start: start ?? (scopedNodeStarts.length ? Math.min(...scopedNodeStarts) : undefined),
      end: terminal ?? undefined,
    };
  };
  const lifecycle = runs.map((run) => lifecycleFor(run));
  const starts = lifecycle.map((item) => item.start).filter((value): value is number => Number.isFinite(value));
  const ends = lifecycle.map((item) => item.end).filter((value): value is number => Number.isFinite(value));
  const startMs = starts.length ? Math.min(...starts) : (nodeStarts.length ? Math.min(...nodeStarts) : 0);
  const endMs = ends.length ? Math.max(...ends) : (nodeEnds.length ? Math.max(...nodeEnds) : startMs);
  const totalMs = Math.max(0, endMs - startMs);

  // Packed time axis: wall-clock idle time BETWEEN runs is compressed out, so
  // the strip is proportional to processing time (a 10s session fills the
  // width; a multi-day session no longer collapses into two dots).
  const spans = runs.map((run, index) => {
    const scoped = nodes.filter((node) => node.runId === run.runId);
    const runStarts = scoped.map((node) => node.startMs).filter(Number.isFinite);
    const runEnds = scoped.map((node) => node.endMs).filter(Number.isFinite);
    const from = lifecycle[index]?.start ?? (runStarts.length ? Math.min(...runStarts) : startMs);
    const to = lifecycle[index]?.end ?? (runEnds.length ? Math.max(...runEnds) : from);
    return {
      turn: run.turn,
      runId: run.runId,
      startMs: from,
      endMs: Math.max(to, from + 200),
    };
  });
  const gapPercent = runs.length > 1 ? 0.5 : 0;
  const available = 100 - gapPercent * (runs.length - 1);
  const busyTotal = spans.reduce((sum, span) => sum + (span.endMs - span.startMs), 0) || 1;
  const turns: TurnWindow[] = [];
  let cursor = 0;
  for (const span of spans) {
    const width = ((span.endMs - span.startMs) / busyTotal) * available;
    turns.push({ ...span, left: cursor, width: Math.max(width, 0.4) });
    cursor += width + gapPercent;
  }

  // Run-level model usage: runtime.result events carry the aggregate tokens
  // for each run; take the max as the session total (retries inflate sums).
  const usage = { inputTokens: 0, outputTokens: 0, known: false };
  for (const run of runs) {
    for (const item of run.activity?.items ?? []) {
      if (item.event_type !== "runtime.result") continue;
      const raw = item.metadata.usage;
      if (!raw || typeof raw !== "object") continue;
      const values = raw as Record<string, unknown>;
      const input = typeof values.input_tokens === "number" ? values.input_tokens : 0;
      const output = typeof values.output_tokens === "number" ? values.output_tokens : 0;
      if (!input && !output) continue;
      usage.known = true;
      usage.inputTokens = Math.max(usage.inputTokens, input);
      usage.outputTokens = Math.max(usage.outputTokens, output);
    }
  }

  return {
    runs,
    nodes,
    window: { startMs, endMs, totalMs },
    turns,
    summary: {
      turns: runs.length,
      toolCalls: nodes.filter((node) => node.badge === "工具").length,
      durationMs: totalMs,
    },
    usage,
  };
}

export function searchTraceNodes(
  nodes: readonly TraceNode[],
  query: string,
): TraceNode[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return [...nodes];
  return nodes.filter((node) =>
    [node.label, node.detail, node.badge, node.output ?? "", node.summary ?? ""]
    .join("\n")
    .toLowerCase()
    .includes(needle),
  );
}

/** Filter groups, rendered as toggles in the console's filter popover. */
export const TRACE_FILTER_GROUPS: ReadonlyArray<{
  key: string;
  label: string;
  badges: readonly string[];
}> = [
  { key: "system", label: "系统提示词", badges: ["系统"] },
  { key: "context", label: "上下文", badges: ["上下文"] },
  { key: "user", label: "用户消息", badges: ["用户"] },
  { key: "thinking", label: "思考过程", badges: ["思考"] },
  { key: "assistant", label: "助手消息", badges: ["助手"] },
  { key: "tool", label: "工具调用", badges: ["工具"] },
  { key: "other", label: "子任务/产物/审批", badges: ["子任务", "产物", "审批", "异常"] },
];

export function traceFilterKey(badge: string): string {
  const group = TRACE_FILTER_GROUPS.find((entry) => entry.badges.includes(badge));
  return group?.key ?? "other";
}

export function filterTraceNodes(
  nodes: readonly TraceNode[],
  enabled: Readonly<Record<string, boolean>>,
): TraceNode[] {
  return nodes.filter((node) => enabled[traceFilterKey(node.badge)] !== false);
}

export function allFiltersEnabled(): Record<string, boolean> {
  return Object.fromEntries(TRACE_FILTER_GROUPS.map((group) => [group.key, true]));
}

/** Percent positions for timeline rendering; clamped, min width for dots. */
// Gapless packed strip: phases that are adjacent in time render touching;
// only real idle (a phase starting later than the previous ended) inserts a
// fixed small gap. Widths are proportional to phase duration, so three 3s
// phases fill exactly the same axis span as 9s of continuous work.
const STRIP_ADJACENT_MS = 500;
const STRIP_GAP_PCT_MAX = 0.8;

export interface StripEntry {
  id: string;
  left: number;
  width?: number;
}

export interface StripLayout {
  blocks: StripEntry[];
  ticks: StripEntry[];
}

export function buildStripLayout(
  phases: ReadonlyArray<Pick<TraceNode, "id" | "startMs" | "endMs" | "lane">>,
  ticks: ReadonlyArray<Pick<TraceNode, "id" | "startMs" | "endMs" | "lane">>,
): StripLayout {
  // Same-lane phases separated by a small gap merge into one strip block:
  // thinking fragments interleave with answers inside one model activity, and
  // sub-second tool latency should not read as idle.
  const laneMergeMs = 2_000;
  const mergedPhases: Array<Pick<TraceNode, "id" | "startMs" | "endMs" | "lane">> = [];
  for (const node of [...phases]
    .filter((node) => node.endMs > node.startMs)
    .sort((a, b) => a.startMs - b.startMs || a.endMs - b.endMs)) {
    const previous = mergedPhases.at(-1);
    if (
      previous &&
      previous.lane === node.lane &&
      node.startMs - previous.endMs <= laneMergeMs
    ) {
      previous.endMs = Math.max(previous.endMs, node.endMs);
      continue;
    }
    mergedPhases.push({ ...node });
  }
  const sorted = mergedPhases;
  let gapCount = 0;
  for (let index = 1; index < sorted.length; index += 1) {
    if (sorted[index].startMs - sorted[index - 1].endMs > STRIP_ADJACENT_MS) gapCount += 1;
  }
  const gapPct = gapCount > 0 ? Math.min(STRIP_GAP_PCT_MAX, 22 / gapCount) : 0;
  const busyTotal =
    sorted.reduce((sum, node) => sum + (node.endMs - node.startMs), 0) || 1;
  const scale = (100 - gapPct * gapCount) / busyTotal;

  const blocks: StripEntry[] = [];
  const anchors: Array<{ start: number; end: number; left: number; width: number }> = [];
  let cursor = 0;
  for (let index = 0; index < sorted.length; index += 1) {
    const node = sorted[index];
    const width = Math.max((node.endMs - node.startMs) * scale, 0.15);
    blocks.push({ id: node.id, left: cursor, width });
    anchors.push({ start: node.startMs, end: node.endMs, left: cursor, width });
    cursor += width;
    const next = sorted[index + 1];
    if (next && next.startMs - node.endMs > STRIP_ADJACENT_MS) cursor += gapPct;
  }

  // Input ticks map through the packed anchors: inside a phase linearly, in a
  // gap at its middle, before/after everything clamped to the edges.
  const mapped = ticks.map((tick) => {
    if (!anchors.length) return { id: tick.id, left: 0 };
    if (tick.startMs <= anchors[0].start) return { id: tick.id, left: anchors[0].left };
    for (const anchor of anchors) {
      if (tick.startMs <= anchor.end) {
        const duration = Math.max(anchor.end - anchor.start, 1);
        const offset = Math.max(tick.startMs - anchor.start, 0) / duration;
        return { id: tick.id, left: anchor.left + offset * anchor.width };
      }
    }
    return { id: tick.id, left: 100 };
  });

  return { blocks, ticks: mapped };
}
