/**
 * 运行状态载荷：有些运行记录会把 `{"status":"ready"}` 这类状态对象原样当成
 * 消息正文发出来。它不是给人读的文本，聊天区和聊天记录都不该照搬 JSON。
 *
 * 只处理「整条正文就是一个只含状态字段的 JSON 对象」这一种情况；
 * 带正文或别的字段的对象、普通文字和代码片段都原样返回。
 */
const STATUS_KEYS = new Set(["status", "state", "phase", "stage", "event"]);

const STATUS_LABEL: Record<string, string> = {
  ready: "已就绪",
  idle: "空闲",
  queued: "排队中",
  pending: "等待中",
  starting: "启动中",
  running: "运行中",
  waiting: "等待确认",
  completed: "已完成",
  done: "已完成",
  succeeded: "已完成",
  success: "已完成",
  failed: "失败",
  error: "失败",
  stopped: "已停止",
  cancelled: "已停止",
  canceled: "已停止",
};

/** 可读状态文案；无法识别的状态载荷返回空串，交由调用方按空消息处理。 */
export function readableGroupMessageText(raw: string): string {
  const text = raw.trim();
  if (!text.startsWith("{") || !text.endsWith("}")) return raw;
  let payload: unknown;
  try {
    payload = JSON.parse(text);
  } catch {
    return raw;
  }
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) return raw;
  const entries = Object.entries(payload as Record<string, unknown>);
  if (entries.length === 0) return raw;
  // 只要还带着 status/state 以外的字段，就可能是用户自己的 JSON，不碰。
  if (entries.some(([key]) => !STATUS_KEYS.has(key))) return raw;
  const status = entries.find(([key]) => STATUS_KEYS.has(key))?.[1];
  if (typeof status !== "string") return "";
  return STATUS_LABEL[status.toLowerCase()] ?? "";
}
