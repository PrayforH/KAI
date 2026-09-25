import {
  type ActivityItem,
  type RunActivity,
  runActivitySchema,
} from "./activity-schema";

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
  entries?: Array<{ name: string; description: string }>;
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
  left: number;
  width: number;
}

export interface SessionTrace {
  runs: SessionTraceRun[];
  nodes: TraceNode[];
  window: { startMs: number; endMs: number; totalMs: number };
  turns: TurnWindow[];
  summary: { turns: number; toolCalls: number; durationMs: number };
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
    const results = new Map<string, ActivityItem>();
    const approvals = new Map<string, ActivityItem>();
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

      if (CONTEXT_EVENT_TYPES.has(item.event_type)) {
        nodes.push({
          id: `context-${run.runId}-${item.id}`,
          runId: run.runId,
          turn: run.turn,
          step: nodes.filter((node) => node.runId === run.runId).length + 1,
          lane: "input",
          badge: "上下文",
          label: item.title || item.event_type,
          detail: preview(item.summary ?? "", 140),
          status: item.status,
          startMs: timestampMs,
          endMs: timestampMs,
          summary: item.summary ?? undefined,
          // The staged-assets event only carries a skill count; the drawer
          // fills the actual list from the resolved draft manifest.
          entries: item.event_type === "agent.assets.staged" ? manifest?.entries : undefined,
          running: false,
        });
        continue;
      }

      const isMessageFrame =
        item.event_type === "message.delta" || item.event_type === "message.completed";
      const isReasoningFrame =
        item.event_type === "reasoning.delta" || item.event_type === "reasoning.completed";
      if (isMessageFrame || isReasoningFrame) {
        const rawId = typeof item.metadata.message_id === "string"
          ? item.metadata.message_id
          : item.id;
        // Reasoning and answer streams can share a message id; keep them as
        // separate spans so 思考过程 stays its own row.
        const messageId = isReasoningFrame ? `reasoning:${rawId}` : `answer:${rawId}`;
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
            startMs: timestampMs,
            endMs: timestampMs,
            output: text,
            status: item.status,
            thinking: isReasoningFrame,
          });
        }
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

    for (const [messageId, span] of spans) {
      nodes.push({
        id: `message-${run.runId}-${messageId}`,
        runId: run.runId,
        turn: run.turn,
        step: nodes.filter((node) => node.runId === run.runId).length + 1,
        lane: "model",
        badge: span.thinking ? "思考" : "助手",
        label: span.thinking ? "思考过程" : "助手",
        detail: preview(span.output, 140),
        status: span.status,
        startMs: span.startMs,
        endMs: span.endMs,
        output: span.output || undefined,
        running: isRunningStatus(span.status),
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

  const starts = nodes.map((node) => node.startMs).filter(Number.isFinite);
  const ends = nodes.map((node) => node.endMs).filter(Number.isFinite);
  const startMs = starts.length ? Math.min(...starts) : 0;
  const endMs = ends.length ? Math.max(...ends) : startMs;
  const totalMs = Math.max(0, endMs - startMs);

  const turns: TurnWindow[] = runs.map((run) => {
    const scoped = nodes.filter((node) => node.runId === run.runId);
    const runStarts = scoped.map((node) => node.startMs).filter(Number.isFinite);
    const runEnds = scoped.map((node) => node.endMs).filter(Number.isFinite);
    const from = runStarts.length ? Math.min(...runStarts) : startMs;
    const to = runEnds.length ? Math.max(...runEnds) : from;
    const left = totalMs > 0 ? ((from - startMs) / totalMs) * 100 : 0;
    const width = totalMs > 0 ? ((to - from) / totalMs) * 100 : 0;
    return {
      turn: run.turn,
      left: Math.min(Math.max(left, 0), 100),
      width: Math.min(Math.max(width, 0.6), 100 - Math.min(Math.max(left, 0), 100)),
    };
  });

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
export function timelinePosition(
  node: Pick<TraceNode, "startMs" | "endMs">,
  window: SessionTrace["window"],
): { left: number; width: number } {
  if (window.totalMs <= 0) return { left: 0, width: 0.8 };
  const left = ((node.startMs - window.startMs) / window.totalMs) * 100;
  const width = Math.max(((node.endMs - node.startMs) / window.totalMs) * 100, 0.8);
  return {
    left: Math.min(Math.max(left, 0), 100),
    width: Math.min(width, 100 - Math.min(Math.max(left, 0), 100)),
  };
}
