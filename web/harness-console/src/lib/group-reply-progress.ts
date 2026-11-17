/**
 * 群回复进度：把既有 run 事件流投影成三档中文阶段。
 *
 * 只读展示。不落库、不改群消息接口：事件流就是主会话用的
 * `GET /agui/runs/{run_id}/events`，群回复的 runId 由消息轮询带回来。
 */

export type GroupReplyStage = "thinking" | "tool" | "writing";

export const GROUP_REPLY_STAGE_LABEL: Record<GroupReplyStage, string> = {
  thinking: "正在思考",
  tool: "正在调用工具",
  writing: "正在整理回复",
};

/** 事件流还没给出任何事实时的兜底文案，与修复前一致。 */
export const GROUP_REPLY_FALLBACK_TEXT = "正在回复…";

type ActivityItem = {
  event_type?: string;
  metadata?: Record<string, unknown> | null;
};

/** 一帧 AG-UI 事件里本模块会读取的字段，其余一律忽略。 */
export type GroupReplyStreamEvent = {
  type?: string;
  activityType?: string;
  content?: unknown;
  patch?: unknown;
  /** AG-UI 标准工具事件用 camelCase 的工具调用 id。 */
  toolCallId?: string;
  /** 工具名只用于判断“是不是真的在调工具”，界面上绝不展示。 */
  toolCallName?: string;
};

/**
 * 跨帧保留的最小状态。`pendingToolCalls` 按工具调用 id 记录还没返回的调用，
 * 并行工具里只要还有一个没回来，就仍算“正在调用工具”。
 */
export type GroupReplyProgress = {
  readonly stage?: GroupReplyStage;
  readonly pendingToolCalls: readonly string[];
};

export const GROUP_REPLY_PROGRESS_EMPTY: GroupReplyProgress = {
  pendingToolCalls: [],
};

/** 只有真正的工具请求 / 返回能决定“正在调用工具”。 */
const TOOL_REQUEST = "tool.request";
const TOOL_RESULT = "tool.result";

/**
 * 后端把 `approval.requested` 同时投影成一帧标准工具调用
 * （mapper.py:157-176，工具名见下），审批通过 / 拒绝才补 TOOL_CALL_RESULT。
 * 照单收进 pending 会让“等人审批”一直被显示成“正在调用工具”，所以按名排除。
 */
const APPROVAL_TOOL_NAMES: ReadonlySet<string> = new Set(["harness_request_approval"]);

/** 审批相关活动条目：等待、通过、拒绝、过期、取消。 */
const APPROVAL_ITEM = /^approval\./;

/**
 * 其余过程事件只说明“还在处理”，归入思考：
 * 运行时初始化、上下文压缩、子 Agent 阶段等都不是工具调用。
 * 刻意不含 `runtime.result`（收尾时会把“整理回复”打回思考）与 `artifact.*`。
 */
const PROCESS_ITEM = /^(?:reasoning\.|run\.(?:queued|started|resumed)|runtime\.system|context\.compaction|subagent\.)/;

/** 模型开始 / 正在 / 完成输出；`message.delta` 也可能是过程中的进展说明。 */
const OUTPUT_ITEM = /^message\.(?:start|delta|completed)$/;

const AGUI_TEXT_EVENTS = new Set([
  "TEXT_MESSAGE_START",
  "TEXT_MESSAGE_CONTENT",
  "TEXT_MESSAGE_END",
]);
const AGUI_THINKING_EVENTS = new Set([
  "THINKING_TEXT_MESSAGE_START",
  "THINKING_TEXT_MESSAGE_CONTENT",
  "THINKING_TEXT_MESSAGE_END",
]);

function toolCallKey(raw: unknown): string | undefined {
  if (typeof raw !== "string") return undefined;
  const key = raw.trim();
  return key === "" ? undefined : key;
}

function itemToolCallKey(item: ActivityItem): string | undefined {
  return toolCallKey(item.metadata?.tool_call_id);
}

function addPending(
  pending: readonly string[],
  key: string | undefined,
): readonly string[] {
  if (!key || pending.includes(key)) return pending;
  return [...pending, key];
}

function removePending(
  pending: readonly string[],
  key: string | undefined,
): readonly string[] {
  if (!key || !pending.includes(key)) return pending;
  return pending.filter((entry) => entry !== key);
}

/**
 * 审批帧落回思考：审批条目自带的 tool_call_id 若是真实工具，
 * 说明这个工具正卡在等人审批，不该继续按“执行中”显示；其余并行工具照常保持工具档。
 */
function settleApproval(
  state: GroupReplyProgress,
  key: string | undefined,
): GroupReplyProgress {
  const pending = removePending(state.pendingToolCalls, key);
  return { stage: pending.length > 0 ? "tool" : "thinking", pendingToolCalls: pending };
}

function isApprovalToolCall(name: unknown): boolean {
  return typeof name === "string" && APPROVAL_TOOL_NAMES.has(name);
}

/** 把一条活动条目并入状态。工具未全部返回时，过程事件不得改档。 */
function applyItem(
  state: GroupReplyProgress,
  item: ActivityItem,
): GroupReplyProgress {
  const type = String(item.event_type ?? "");
  if (!type) return state;

  if (APPROVAL_ITEM.test(type)) return settleApproval(state, itemToolCallKey(item));
  if (type === TOOL_REQUEST) {
    return {
      stage: "tool",
      pendingToolCalls: addPending(state.pendingToolCalls, itemToolCallKey(item)),
    };
  }
  if (type === TOOL_RESULT) {
    const pending = removePending(state.pendingToolCalls, itemToolCallKey(item));
    // 并行工具：任一未完成都保持工具档，全部返回才回到思考。
    return { stage: pending.length > 0 ? "tool" : "thinking", pendingToolCalls: pending };
  }
  if (state.pendingToolCalls.length > 0) return state;
  if (OUTPUT_ITEM.test(type)) return { ...state, stage: "writing" };
  if (PROCESS_ITEM.test(type)) return { ...state, stage: "thinking" };
  return state;
}

/**
 * 从一份完整的活动快照重建状态（含并行工具集合）。
 * 快照是替换语义：不在快照里的工具调用不会继续被当成未完成。
 */
export function progressFromActivity(content: unknown): GroupReplyProgress {
  const items = (content as { items?: unknown } | null)?.items;
  if (!Array.isArray(items)) return GROUP_REPLY_PROGRESS_EMPTY;
  return items.reduce<GroupReplyProgress>(
    (state, raw) => applyItem(state, (raw ?? {}) as ActivityItem),
    GROUP_REPLY_PROGRESS_EMPTY,
  );
}

/** 只要阶段时用这个；并行工具的完整状态请用 {@link progressFromActivity}。 */
export function stageFromActivity(content: unknown): GroupReplyStage | undefined {
  return progressFromActivity(content).stage;
}

function applyDelta(patch: unknown, state: GroupReplyProgress): GroupReplyProgress {
  if (!Array.isArray(patch)) return state;
  let next = state;
  for (const change of patch) {
    const operation = change as { op?: string; path?: string; value?: unknown } | null;
    if (operation?.op !== "add" || operation.path !== "/items/-") continue;
    next = applyItem(next, (operation.value ?? {}) as ActivityItem);
  }
  return next;
}

function isRunActivity(event: GroupReplyStreamEvent): boolean {
  return event.activityType === "harness.run.v1" || event.activityType === undefined;
}

export function isTerminalEvent(event: GroupReplyStreamEvent): boolean {
  return event?.type === "RUN_FINISHED" || event?.type === "RUN_ERROR";
}

/**
 * 合并一帧事件，返回新的完整状态。事件里绝不读取工具名或参数，只按类别判断。
 *
 * 标准 AG-UI 事件与活动投影会同时到达：工具调用 id 相同，重复的加入 / 移除
 * 都是幂等的。`TOOL_CALL_END` 只是参数帧结束（mapper.py 与 START/ARGS 同批
 * 发出），不代表工具执行完成，因此不改阶段。
 */
export function nextGroupReplyProgress(
  state: GroupReplyProgress,
  event: GroupReplyStreamEvent,
): GroupReplyProgress {
  switch (event?.type) {
    case "ACTIVITY_SNAPSHOT":
      return isRunActivity(event) ? progressFromActivity(event.content) : state;
    case "ACTIVITY_DELTA":
      return isRunActivity(event) ? applyDelta(event.patch, state) : state;
    case "TOOL_CALL_START": {
      const key = toolCallKey(event.toolCallId);
      // 审批被投影成标准工具帧，但它不是工具执行，不能进 pending。
      if (isApprovalToolCall(event.toolCallName)) return settleApproval(state, key);
      return { stage: "tool", pendingToolCalls: addPending(state.pendingToolCalls, key) };
    }
    case "TOOL_CALL_RESULT": {
      const pending = removePending(state.pendingToolCalls, toolCallKey(event.toolCallId));
      return { stage: pending.length > 0 ? "tool" : "thinking", pendingToolCalls: pending };
    }
    case "TOOL_CALL_END":
    case "TOOL_CALL_ARGS":
      return state;
    case "RUN_STARTED":
      return state.stage ? state : { ...state, stage: "thinking" };
    default:
      if (AGUI_TEXT_EVENTS.has(event?.type ?? "")) {
        return state.pendingToolCalls.length > 0 ? state : { ...state, stage: "writing" };
      }
      if (AGUI_THINKING_EVENTS.has(event?.type ?? "")) {
        return state.pendingToolCalls.length > 0 ? state : { ...state, stage: "thinking" };
      }
      return state;
  }
}

export type SseFrame = { id?: string; data: string };

/**
 * 从缓冲区里切出完整的 SSE 帧，返回未完成的部分。
 * 只有 `data:` 行是 JSON 负载，其余（`id:`、注释心跳）按流协议处理。
 */
export function takeSseFrames(buffer: string): { frames: SseFrame[]; rest: string } {
  const frames: SseFrame[] = [];
  const normalized = buffer.replace(/\r\n/g, "\n");
  const parts = normalized.split("\n\n");
  const rest = parts.pop() ?? "";
  for (const part of parts) {
    let id: string | undefined;
    const data: string[] = [];
    for (const line of part.split("\n")) {
      if (line.startsWith("id:")) id = line.slice(3).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
    }
    if (data.length > 0) frames.push({ id, data: data.join("\n") });
  }
  return { frames, rest };
}
