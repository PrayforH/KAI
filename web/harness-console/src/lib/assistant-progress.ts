import { assistantCopy } from "./assistant-copy";

export type ActivityLike = {
  id?: string | null;
  event_type: string;
  title?: string | null;
  summary?: string | null;
  status?: string | null;
  timestamp?: string;
  metadata?: Record<string, unknown> | null;
};
type ProgressActivity = { status?: string; items: ReadonlyArray<ActivityLike> };
export type ChatActivityStep = { id: string; text: string; tone: "note" };
const PROCESS = /^(?:tool|runtime|context|approval|subagent|artifact)\./;
const ACTIVE = new Set(["queued", "pending", "provisioning", "running", "waiting", "waiting_approval", "cancelling"]);
const TRANSIENT = /^(?:正在等待(?:本轮模型结果|模型输出结果)|正在生成回复|正在准备运行(?:环境)?|正在整理本轮结果|正在生成可恢复的上下文摘要|正在处理(?:你的请求)?)[。.]?$/;

export function isTransientProgress(text: string): boolean {
  return TRANSIENT.test(text.trim().replace(/[（(].*$/, ""));
}

/** Public display text only: never inspect tool arguments, previews or raw reasoning. */
export function trimConclusion(text: string, limit = 96): string {
  const clean = assistantCopy(text.replace(/\[([^\]]+)\]\([^)]+\)/g, "$1"))
    .replace(/```[\s\S]*?(?:```|$)/g, "")
    .replace(/\bmcp__[^\s，。；]+/g, "")
    .replace(/[*#`]+/g, "").replace(/\s+/g, " ").trim();
  const chars = Array.from(clean);
  if (chars.length <= limit) return clean;
  const head = chars.slice(0, Math.max(1, limit - 1)).join("");
  const boundary = Math.max(head.lastIndexOf("。"), head.lastIndexOf("；"), head.lastIndexOf("！"), head.lastIndexOf("？"));
  return boundary >= limit / 3 ? head.slice(0, boundary + 1) : `${head}…`;
}

function publicNotes(activity: ProgressActivity): Array<ChatActivityStep & { index: number }> {
  const boundary = activity.items.reduce((last, item, index) => PROCESS.test(item.event_type) ? index : last, -1);
  return activity.items.flatMap((item, index) => {
    // Only the provider's explicit public summary channel may appear before a tool.
    // Ordinary text after the last process event belongs to the answer bubble.
    if (item.event_type !== "reasoning.summary.delta" && !(item.event_type === "message.delta" && index < boundary)) return [];
    const text = trimConclusion(item.summary ?? "");
    return text && !isTransientProgress(text) ? [{ id: item.id || `progress-${index}`, text, tone: "note" as const, index }] : [];
  });
}

function toolStage(name: string): [string, string] {
  if (/assistant_route_message/i.test(name)) return ["正在安排处理。", "已确定处理方式。"];
  if (/^(?:Task|Agent)$/.test(name)) return ["子助理正在处理请求。", "已收到子助理结果，正在核对。"];
  if (/assistant_(?:delegate|create|send_instruction)/i.test(name)) return ["正在联系子助理。", "已收到交办回执，正在核对。"];
  if (/assistant_|managed_|objective_/i.test(name)) return ["正在核对子助理和原任务。", "已收到查询结果，正在整理。"];
  if (/search|retriev|knowledge|grep|glob/i.test(name)) return ["正在检索相关资料。", "检索结果已返回，正在核对。"];
  if (/artifact|publish|export|download/i.test(name)) return ["正在整理交付文件。", "文件处理结果已返回，正在核对。"];
  if (/bash|shell|command|exec/i.test(name)) return ["正在执行所需命令。", "命令已有返回，正在核对结果。"];
  if (/browser|browse|navigate|web_fetch/i.test(name)) return ["正在查看相关页面。", "页面信息已返回，正在整理。"];
  if (/read|file_get|list_files/i.test(name)) return ["正在读取所需文件。", "文件内容已返回，正在整理。"];
  if (/write|edit|file_put/i.test(name)) return ["正在更新相关文件。", "文件操作已有结果，正在核对。"];
  if (/computer|desktop|screen/i.test(name)) return ["正在操作云电脑。", "电脑操作已有返回，正在核对。"];
  return ["正在处理这一步。", "这一步已有结果，正在整理。"];
}

function processState(activity: ProgressActivity) {
  const pending = new Map<string, { item: ActivityLike; index: number }>();
  const approvals = new Set<string>();
  let result: { item: ActivityLike; index: number; request?: ActivityLike } | undefined;
  activity.items.forEach((item, index) => {
    const id = String(item.metadata?.tool_call_id ?? "legacy");
    const approval = String(item.metadata?.approval_id ?? "legacy");
    if (item.event_type === "tool.request") pending.set(id, { item, index });
    if (item.event_type === "tool.result") {
      result = { item, index, request: pending.get(id)?.item };
      pending.delete(id);
    }
    if (item.event_type === "approval.requested") approvals.add(approval);
    else if (/^approval\.(?:approved|rejected|expired|cancelled)$/.test(item.event_type)) approvals.delete(approval);
  });
  return { pending: [...pending.values()].at(-1), approval: approvals.size > 0, result };
}

export function progressPhrase(activity: ProgressActivity, options: { allowTransient?: boolean } = {}): string {
  const { pending, approval, result } = processState(activity);
  if (approval || activity.status === "waiting_approval") return "需要你确认后才能继续。";
  if (activity.status === "cancelling") return "已请求停止，正在等待当前操作退出。";
  const latest = activity.items.findLast(item => PROCESS.test(item.event_type));
  if (latest?.event_type === "context.compaction.started" || latest?.summary === "正在生成可恢复的上下文摘要") return "正在整理历史上下文，保留本次任务的信息。";
  if (latest?.event_type === "approval.rejected") return "本次操作未获允许，正在结束这一步。";
  if (latest?.event_type === "approval.expired") return "确认已过期，正在结束这一步。";
  if (latest?.status === "failed") {
    const detail = trimConclusion(latest.summary ?? "", 64);
    return detail ? `这一步遇到问题：${detail}` : "这一步遇到问题，正在核对原因。";
  }
  if (latest?.event_type === "subagent.started") return "子助理正在处理，等待它返回进展。";
  if (latest?.event_type === "artifact.ready") return "交付文件已准备好，正在整理说明。";
  if (latest?.event_type === "runtime.system" && /^(?:retrying|reconnecting)$/i.test(latest.summary ?? "")) return "请求正在重试，等待新的结果。";
  if (pending) return toolStage(String(pending.item.metadata?.name ?? ""))[0];
  const notes = publicNotes(activity);
  const note = notes.at(-1);
  if (note && (!result || note.index > result.index)) return note.text;
  if (result) {
    const text = trimConclusion(result.item.summary ?? "");
    return text && !/^返回\s*\d|^工具调用/.test(text) ? text : toolStage(String(result.request?.metadata?.name ?? ""))[1];
  }
  if (note) return note.text;
  if (options.allowTransient === false) return "";
  if (activity.items.some(item => item.event_type === "run.running")) return "正在思考";
  if (activity.items.some(item => item.event_type === "run.provisioning")) return "收到这条消息了，正在准备处理。";
  if (["queued", "pending"].includes(activity.status ?? "")) return "收到这条消息了，等候开始处理。";
  if (activity.status === "provisioning") return "正在连接本次运行环境。";
  if (activity.items.some(item => item.event_type === "runtime.system" && (isTransientProgress(item.summary ?? "") || item.title === "模型正在处理"))) return "正在处理你的请求。";
  return "";
}

/**
 * 状态行短语：只留短阶段，去掉句末标点。
 * 「正在检索相关资料。」在状态行里显示成「正在检索相关资料」。
 */
export function statusPhrase(text: string): string {
  return trimConclusion(text).replace(/[。．.！!？?，,、；;：:]+$/u, "");
}

export function activitySteps(activity: ProgressActivity, options: { limit?: number; excludeText?: string } = {}): ChatActivityStep[] {
  const exclude = trimConclusion(options.excludeText ?? "");
  const unique = new Map<string, ChatActivityStep>();
  for (const { index: _, ...note } of publicNotes(activity)) {
    if (note.text !== exclude) { unique.delete(note.text); unique.set(note.text, note); }
  }
  return [...unique.values()].slice(-Math.max(1, Math.min(3, options.limit ?? 3)));
}

export function shouldShowChatActivityRow(status: string, activity: ProgressActivity, options: { allowTransient?: boolean } = {}): boolean {
  return ACTIVE.has(status) && Boolean(progressPhrase({ ...activity, status }, options));
}
export function shouldShowChatActivitySteps(status: string, activity: ProgressActivity, options: { allowTransient?: boolean } = {}): boolean {
  return shouldShowChatActivityRow(status, activity, options) && activitySteps(activity, { excludeText: progressPhrase(activity, options) }).length > 0;
}


/** A clock can report a real wait, but must never invent the next task stage. */
export function modelWaitingSince(activity: ProgressActivity): string | undefined {
  const latest = activity.items.findLast(item => PROCESS.test(item.event_type));
  if (latest?.event_type !== "runtime.system" || !(isTransientProgress(latest.summary ?? "") || latest.title === "模型正在处理")) return;
  const state = processState(activity);
  if (state.pending || state.approval) return;
  return latest.timestamp && Number.isFinite(Date.parse(latest.timestamp)) ? latest.timestamp : undefined;
}

export function hasPendingProgressAction(activity: ProgressActivity): boolean {
  const state = processState(activity);
  return Boolean(state.pending || state.approval);
}
