import type { ActivityItem, RunActivity } from "./activity-schema";

export type RunPhase =
  | "queued"
  | "running"
  | "waiting_approval"
  | "completed"
  | "failed"
  | "rejected"
  | "cancelled";

export type WorkStatus = "running" | "waiting" | "completed" | "failed";

export interface RunTaskNode {
  id: string;
  parentId?: string;
  title: string;
  status: WorkStatus;
  sequence: number;
  alias?: string;
  agentVersion?: string;
  durationMs?: number;
  tokens?: number;
  costUsd?: number;
  toolUses?: number;
  errorCode?: string;
}

export interface RunCitation {
  index: number;
  chunkId: string;
  documentId?: string;
  sourceReference: string;
  knowledgeBaseReference?: string;
  sourceDisplayName?: string;
  snapshotId?: string;
  title?: string;
  uri?: string;
  score?: number;
  content: string;
}

export interface RunToolNode {
  id: string;
  name: string;
  status: WorkStatus;
  sequence: number;
  arguments?: Record<string, unknown>;
  resultSummary?: string;
  resultPreview?: string;
  citations?: RunCitation[];
}

export interface RunViewModel {
  runId: string;
  phase: RunPhase;
  startedAt: string;
  updatedAt: string;
  elapsedMs: number;
  summary: string;
  items: ActivityItem[];
  tasks: RunTaskNode[];
  tools: RunToolNode[];
  taskCount: number;
  toolCount: number;
  totalTokens?: number;
  totalCostUsd?: number;
  turns?: number;
  stopReason?: string;
  failureCode?: string;
  pendingApprovalId?: string;
  queueReason?: string;
  queueReasonCode?: string;
  blockedByRunId?: string;
}

const terminalPhases = new Set<RunPhase>([
  "completed",
  "failed",
  "rejected",
  "cancelled",
]);

function mergedItems(
  previous: RunViewModel | undefined,
  activity: RunActivity,
): ActivityItem[] {
  const byId = new Map<string, ActivityItem>();
  if (previous?.runId === activity.run_id) {
    for (const item of previous.items) byId.set(item.id, item);
  }
  for (const item of activity.items) byId.set(item.id, item);
  return [...byId.values()].sort((left, right) => left.sequence - right.sequence);
}

function terminalPhase(items: readonly ActivityItem[]): RunPhase | undefined {
  for (const item of [...items].reverse()) {
    if (item.event_type === "run.succeeded") return "completed";
    if (item.event_type === "run.cancelled") return "cancelled";
    if (item.event_type === "run.rejected") return "rejected";
    if (item.event_type === "run.failed" || item.event_type === "run.timed_out") {
      return "failed";
    }
  }
  return undefined;
}

function pendingApproval(items: readonly ActivityItem[]): string | undefined {
  const pending = new Map<string, string>();
  for (const item of items) {
    const approvalId = item.metadata.approval_id;
    if (typeof approvalId !== "string") continue;
    if (item.event_type === "approval.requested") {
      pending.set(approvalId, approvalId);
    } else if (
      item.event_type === "approval.approved" ||
      item.event_type === "approval.rejected"
    ) {
      pending.delete(approvalId);
    }
  }
  return [...pending.keys()].at(-1);
}

function phaseFor(
  previous: RunViewModel | undefined,
  items: readonly ActivityItem[],
): RunPhase {
  if (previous && terminalPhases.has(previous.phase)) return previous.phase;
  const terminal = terminalPhase(items);
  if (terminal) return terminal;
  if (pendingApproval(items)) return "waiting_approval";
  const latestRun = [...items].reverse().find((item) => item.event_type.startsWith("run."));
  return latestRun?.event_type === "run.queued" ? "queued" : "running";
}

function workStatus(item: ActivityItem): WorkStatus {
  if (item.status === "failed") return "failed";
  if (item.status === "waiting") return "waiting";
  if (item.status === "succeeded" || item.status === "completed") return "completed";
  return "running";
}

function taskNodes(items: readonly ActivityItem[]): RunTaskNode[] {
  const tasks = new Map<string, RunTaskNode>();
  const delegatedToolCalls = new Set(
    items.flatMap((item) => {
      const taskId = item.metadata.task_id;
      const parentId = item.metadata.parent_tool_use_id;
      return item.kind === "subagent" &&
        typeof taskId === "string" &&
        typeof parentId === "string"
        ? [parentId]
        : [];
    }),
  );
  for (const item of items) {
    // A denied Agent/Task never emits SDK subagent lifecycle events.
    // Its tool.result still owns the terminal state of the attempted delegation.
    if (item.event_type === "tool.result") {
      const callId = item.metadata.tool_call_id;
      const attempted = typeof callId === "string" ? tasks.get(callId) : undefined;
      if (attempted) {
        const detail = item.metadata.result_preview ?? item.metadata.result_summary;
        tasks.set(attempted.id, {
          ...attempted,
          status: workStatus(item),
          title: typeof detail === "string" && detail ? detail : attempted.title,
        });
      }
      continue;
    }
    if (item.kind !== "subagent") continue;
    const realTaskId = item.metadata.task_id;
    const toolCallId = item.metadata.tool_call_id;
    if (
      typeof realTaskId !== "string" &&
      typeof toolCallId === "string" &&
      delegatedToolCalls.has(toolCallId)
    ) {
      continue;
    }
    const taskId = realTaskId ?? toolCallId ?? item.id;
    if (typeof taskId !== "string") continue;
    const parent = item.metadata.parent_tool_use_id;
    const existing = tasks.get(taskId);
    tasks.set(taskId, {
      id: taskId,
      parentId: typeof parent === "string" ? parent : undefined,
      title: item.summary || item.title,
      status: workStatus(item),
      sequence: existing?.sequence ?? item.sequence,
      alias: typeof item.metadata.alias === "string" ? item.metadata.alias : undefined,
      agentVersion:
        typeof item.metadata.agent_version === "string"
          ? item.metadata.agent_version
          : undefined,
      durationMs:
        typeof item.metadata.duration_ms === "number"
          ? item.metadata.duration_ms
          : undefined,
      tokens:
        typeof item.metadata.usage === "object" &&
        item.metadata.usage !== null &&
        typeof (item.metadata.usage as { total_tokens?: unknown }).total_tokens === "number"
          ? (item.metadata.usage as { total_tokens: number }).total_tokens
          : undefined,
      costUsd:
        typeof item.metadata.cost_usd === "number"
          ? item.metadata.cost_usd
          : undefined,
      toolUses:
        typeof item.metadata.usage === "object" &&
        item.metadata.usage !== null &&
        typeof (item.metadata.usage as { tool_uses?: unknown }).tool_uses === "number"
          ? (item.metadata.usage as { tool_uses: number }).tool_uses
          : undefined,
      errorCode:
        typeof item.metadata.error_code === "string"
          ? item.metadata.error_code
          : undefined,
    });
  }
  return [...tasks.values()].sort((left, right) => left.sequence - right.sequence);
}

function runCitations(value: unknown): RunCitation[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const citations: RunCitation[] = [];
  for (const entry of value) {
    if (typeof entry !== "object" || entry === null) continue;
    const record = entry as Record<string, unknown>;
    if (typeof record.chunkId !== "string" || typeof record.sourceReference !== "string") {
      continue;
    }
    citations.push({
      index: typeof record.index === "number" ? record.index : citations.length + 1,
      chunkId: record.chunkId,
      documentId: typeof record.documentId === "string" ? record.documentId : undefined,
      sourceReference: record.sourceReference,
      knowledgeBaseReference:
        typeof record.knowledgeBaseReference === "string"
          ? record.knowledgeBaseReference
          : undefined,
      sourceDisplayName:
        typeof record.sourceDisplayName === "string" ? record.sourceDisplayName : undefined,
      snapshotId: typeof record.snapshotId === "string" ? record.snapshotId : undefined,
      title: typeof record.title === "string" ? record.title : undefined,
      uri: typeof record.uri === "string" ? record.uri : undefined,
      score: typeof record.score === "number" ? record.score : undefined,
      content: typeof record.content === "string" ? record.content : "",
    });
  }
  return citations.length > 0 ? citations : undefined;
}

function toolNodes(items: readonly ActivityItem[]): RunToolNode[] {
  const tools = new Map<string, RunToolNode>();
  for (const item of items) {
    const rawToolCallId = item.metadata.tool_call_id;
    const toolCallId =
      typeof rawToolCallId === "string"
        ? rawToolCallId
        : item.event_type === "tool.request"
          ? item.id
          : undefined;
    if (!toolCallId) continue;
    if (item.event_type === "tool.request" && item.kind === "tool") {
      const argumentsValue = item.metadata.arguments;
      tools.set(toolCallId, {
        id: toolCallId,
        name: typeof item.metadata.name === "string" ? item.metadata.name : "工具",
        status: "running",
        sequence: item.sequence,
        arguments:
          typeof argumentsValue === "object" &&
          argumentsValue !== null &&
          !Array.isArray(argumentsValue)
            ? argumentsValue as Record<string, unknown>
            : undefined,
      });
      continue;
    }
    if (item.event_type === "approval.requested") {
      const tool = tools.get(toolCallId);
      if (tool) tools.set(toolCallId, { ...tool, status: "waiting" });
      continue;
    }
    if (item.event_type === "tool.result") {
      const tool = tools.get(toolCallId);
      if (tool) {
        tools.set(toolCallId, {
          ...tool,
          status: item.status === "failed" ? "failed" : "completed",
          resultSummary:
            typeof item.metadata.result_summary === "string"
              ? item.metadata.result_summary
              : undefined,
          resultPreview:
            typeof item.metadata.result_preview === "string"
              ? item.metadata.result_preview
              : undefined,
          citations: runCitations(item.metadata.citations),
        });
      }
    }
  }
  return [...tools.values()].sort((left, right) => left.sequence - right.sequence);
}

export function reduceRunViewModel(
  previous: RunViewModel | undefined,
  activity: RunActivity,
): RunViewModel {
  const items = mergedItems(previous, activity);
  const tasks = taskNodes(items);
  const tools = toolNodes(items);
  const lastTimestamp = items.at(-1)?.timestamp ?? activity.started_at;
  const started = Date.parse(activity.started_at);
  const updated = Date.parse(lastTimestamp);
  const latestActive = [...items]
    .reverse()
    .find(
      (item) =>
        ![
          "run.queued",
          "run.provisioning",
          // Routing is an auditable per-Run fact, not a user-visible unit of
          // work. Keep it in Run details without replacing the active status.
          "model.route.selected",
        ].includes(item.event_type),
    );
  const runtimeResult = [...items]
    .reverse()
    .find((item) => item.event_type === "runtime.result");
  const runFailure = [...items]
    .reverse()
    .find((item) => item.status === "failed");
  const queued = items.find((item) => item.event_type === "run.queued");
  const usage = runtimeResult?.metadata.usage;
  const totalTokens =
    typeof usage === "object" &&
    usage !== null &&
    typeof (usage as { total_tokens?: unknown }).total_tokens === "number"
      ? (usage as { total_tokens: number }).total_tokens
      : undefined;
  return {
    runId: activity.run_id,
    phase: phaseFor(previous?.runId === activity.run_id ? previous : undefined, items),
    startedAt: activity.started_at,
    updatedAt: lastTimestamp,
    elapsedMs:
      Number.isFinite(started) && Number.isFinite(updated)
        ? Math.max(0, updated - started)
        : 0,
    summary: latestActive?.summary?.trim() || latestActive?.title || "准备执行",
    items,
    tasks,
    tools,
    taskCount: tasks.length,
    toolCount: tools.length,
    totalTokens,
    totalCostUsd:
      typeof runtimeResult?.metadata.cost_usd === "number"
        ? runtimeResult.metadata.cost_usd
        : undefined,
    turns:
      typeof runtimeResult?.metadata.turns === "number"
        ? runtimeResult.metadata.turns
        : undefined,
    stopReason:
      typeof runtimeResult?.metadata.stop_reason === "string"
        ? runtimeResult.metadata.stop_reason
        : undefined,
    failureCode:
      typeof runFailure?.metadata.error_code === "string"
        ? runFailure.metadata.error_code
        : undefined,
    pendingApprovalId: pendingApproval(items),
    queueReason:
      typeof queued?.summary === "string" ? queued.summary : undefined,
    queueReasonCode:
      typeof queued?.metadata.reason_code === "string"
        ? queued.metadata.reason_code
        : undefined,
    blockedByRunId:
      typeof queued?.metadata.blocked_by_run_id === "string"
        ? queued.metadata.blocked_by_run_id
        : undefined,
  };
}

export function selectComposerDisabled(
  model: Pick<RunViewModel, "phase"> | undefined,
): boolean {
  return model?.phase === "queued" ||
    model?.phase === "running" ||
    model?.phase === "waiting_approval";
}
