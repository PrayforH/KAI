/**
 * Run states the console renders, mirroring the backend's `RunStatus`
 * (`harness.core.models`) and its `terminal_statuses()` partition.
 *
 * These sets were copied into five components before they lived here, and the copies
 * had already drifted: a new terminal state would leave some views polling a run that
 * had stopped, and others showing it as failed. Add a state in one place.
 */

export type StudioRunStatus =
  | "queued"
  | "provisioning"
  | "running"
  | "waiting_approval"
  | "cancelling"
  | "cancelled"
  | "succeeded"
  | "failed"
  | "timed_out"
  | "rejected";

export const TERMINAL_RUN_STATUSES: readonly StudioRunStatus[] = [
  "cancelled",
  "succeeded",
  "failed",
  "timed_out",
  "rejected",
];

/** Terminal without success: the run stopped and did not deliver. */
export const FAILED_RUN_STATUSES: readonly StudioRunStatus[] = [
  "failed",
  "timed_out",
  "rejected",
];

const TERMINAL = new Set<string>(TERMINAL_RUN_STATUSES);
const FAILED = new Set<string>(FAILED_RUN_STATUSES);

export function isTerminalRunStatus(status: string | null | undefined): boolean {
  return status != null && TERMINAL.has(status);
}

export function isFailedRunStatus(status: string | null | undefined): boolean {
  return status != null && FAILED.has(status);
}

export const RUN_STATUS_LABELS: Record<StudioRunStatus, string> = {
  queued: "排队中",
  provisioning: "准备中",
  running: "正在运行",
  waiting_approval: "等待审批",
  cancelling: "正在停止",
  cancelled: "已停止",
  succeeded: "已完成",
  failed: "运行失败",
  timed_out: "已超时",
  rejected: "被拒绝",
};

export function runStatusLabel(status: string | null | undefined): string {
  return RUN_STATUS_LABELS[status as StudioRunStatus] ?? status ?? "";
}

/**
 * Why a turn did not finish, in the user's terms.
 *
 * An unrecognised code must not be shown raw: `error_code` is the platform's internal
 * vocabulary, and an operator reading "runtime_error" learns nothing they can act on.
 */
export function runFailureMessage(errorCode?: string | null): string {
  if (errorCode === "runtime_error" || errorCode === "sandbox_unavailable") {
    return "运行环境未能启动，请检查模型渠道与部署配置后重新试跑。";
  }
  if (errorCode === "runtime_timeout") {
    return "试跑超时，运行已安全停止；可缩小任务范围后重试。";
  }
  if (errorCode === "quota_exceeded") {
    return "本次运行超出配额，请调整配额或稍后重试。";
  }
  return "试跑未通过，请调整要求后再试；详细原因见执行详情。";
}
