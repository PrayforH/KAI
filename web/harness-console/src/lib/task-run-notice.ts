import type { RunActivity } from "./activity-schema";
import type { RunStreamSnapshot } from "./run-stream-store";

export function taskRunNotice(activity: RunActivity | undefined, stream: RunStreamSnapshot, fallback?: string) {
  const freshStream = stream.status === "running" && stream.runId !== activity?.run_id;
  const state = freshStream ? "running" : activity?.status ?? fallback;
  const runId = stream.runId ?? activity?.run_id;
  // A broken SSE connection is not evidence that the server task failed.
  if (stream.status === "error" && (!activity || !stream.runId || stream.runId === activity.run_id) && !["failed", "rejected", "timed_out"].includes(state ?? "")) {
    return { runId, label: "连接中断", tone: "warning", detail: "连接已中断，任务可能仍在运行。请刷新页面确认最新状态。" };
  }
  if (["failed", "rejected", "timed_out"].includes(state ?? "")) {
    const failure = [...(activity?.items ?? [])].reverse().find(item => ["run.failed", "run.rejected", "run.timed_out"].includes(item.event_type));
    const code = failure?.metadata.error_code;
    const detail = code === "runtime_message_too_large"
      ? "批量图片处理产生的运行消息超过容量限制，请继续当前任务并缩小每批处理范围。"
      : code === "runtime_invalid_json" || failure?.metadata.error_type === "CLIJSONDecodeError"
        ? "运行消息过大或格式异常，解析失败。请重试当前任务；批量图片可分批处理。"
        : failure?.metadata.subtype === "error_max_turns"
          ? "该轮触发了历史回合数上限，平台现已取消这项限制，请继续当前任务。"
          : failure?.summary || (state === "timed_out"
          ? "任务执行超时，请继续当前任务，或缩小本轮处理范围。"
          : "任务运行失败，请重试当前任务；如仍失败，可打开运行详情查看原因。");
    return { runId, label: state === "timed_out" ? "运行超时" : state === "rejected" ? "请求被拒绝" : "运行失败", tone: "error", detail };
  }
  const labels: Record<string, string> = {
    queued: "等待运行", provisioning: "准备运行", running: "正在运行",
    waiting_approval: "等待审批", cancelling: "正在停止", cancelled: "已停止",
    succeeded: "已完成", completed: "已完成",
  };
  if (!state || !labels[state]) return null;
  return { runId, label: labels[state], tone: ["running", "provisioning", "queued"].includes(state) ? "active" : "neutral", detail: null };
}
