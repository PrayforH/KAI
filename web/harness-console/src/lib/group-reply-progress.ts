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

// 与主会话活动投影同一组“过程事件”，只用于判断阶段，不展示细节。
const PROCESS_ITEM = /^(?:tool|runtime|context|approval|subagent|artifact)\./;

type StreamEvent = {
  type?: string;
  activityType?: string;
  content?: unknown;
  patch?: unknown;
};

/**
 * 从一份完整的活动快照判断阶段：
 * - 有未完成的工具请求 → 正在调用工具；
 * - 最后一个过程事件之后已有正文 → 正在整理回复；
 * - 其余（推理、排队、工具刚返回）→ 正在思考。
 */
export function stageFromActivity(content: unknown): GroupReplyStage | undefined {
  const items = (content as { items?: unknown } | null)?.items;
  if (!Array.isArray(items)) return undefined;
  const pending = new Set<string>();
  let lastText = -1;
  let lastProcess = -1;
  items.forEach((raw, index) => {
    const item = (raw ?? {}) as ActivityItem;
    const type = String(item.event_type ?? "");
    const callId = String(item.metadata?.tool_call_id ?? "legacy");
    if (type === "tool.request") pending.add(callId);
    else if (type === "tool.result") pending.delete(callId);
    if (type === "message.delta") lastText = index;
    if (PROCESS_ITEM.test(type)) lastProcess = index;
  });
  if (pending.size > 0) return "tool";
  if (lastText > -1 && lastText > lastProcess) return "writing";
  return "thinking";
}

/** 单个增量事件里，最后一条过程条目决定当前阶段。 */
function stageFromItem(raw: unknown): GroupReplyStage | undefined {
  const type = String((raw as ActivityItem | null)?.event_type ?? "");
  if (!type) return undefined;
  if (type === "tool.request") return "tool";
  if (type === "tool.result") return "thinking";
  if (type === "message.delta") return "writing";
  if (PROCESS_ITEM.test(type)) return "tool";
  if (type.startsWith("reasoning.") || type.startsWith("run.")) return "thinking";
  return undefined;
}

function stageFromDelta(patch: unknown): GroupReplyStage | undefined {
  if (!Array.isArray(patch)) return undefined;
  let stage: GroupReplyStage | undefined;
  for (const change of patch) {
    const operation = change as { op?: string; path?: string; value?: unknown } | null;
    if (operation?.op !== "add" || operation.path !== "/items/-") continue;
    stage = stageFromItem(operation.value) ?? stage;
  }
  return stage;
}

export function isTerminalEvent(event: StreamEvent): boolean {
  return event?.type === "RUN_FINISHED" || event?.type === "RUN_ERROR";
}

/**
 * 合并一帧事件。返回 `current` 表示这一帧没有改变阶段（心跳、状态补丁等）。
 * 事件里绝不读取工具名或参数，只按事件类别判断。
 */
export function nextGroupReplyStage(
  current: GroupReplyStage | undefined,
  event: StreamEvent,
): GroupReplyStage | undefined {
  switch (event?.type) {
    case "ACTIVITY_SNAPSHOT":
      return (event.activityType === "harness.run.v1" || event.activityType === undefined)
        ? stageFromActivity(event.content) ?? current
        : current;
    case "ACTIVITY_DELTA":
      return (event.activityType === "harness.run.v1" || event.activityType === undefined)
        ? stageFromDelta(event.patch) ?? current
        : current;
    case "TOOL_CALL_START":
      return "tool";
    case "TOOL_CALL_END":
    case "TOOL_CALL_RESULT":
      return "thinking";
    case "TEXT_MESSAGE_CONTENT":
      return "writing";
    case "THINKING_TEXT_MESSAGE_CONTENT":
      return "thinking";
    case "RUN_STARTED":
      return current ?? "thinking";
    default:
      return current;
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
