const ACTIVE_TASK_STATUSES = new Set([
  "queued",
  "provisioning",
  "running",
  "waiting_approval",
  "cancelling",
]);

export const ACTIVE_TASK_REFRESH_MS = 4_000;
export const IDLE_TASK_REFRESH_MS = 30_000;
export const ERROR_TASK_REFRESH_MS = 10_000;

export function taskListRefreshDelay(
  taskStatuses: readonly string[],
  currentRunPhase?: string,
  failed = false,
): number {
  if (failed) return ERROR_TASK_REFRESH_MS;
  if (
    (currentRunPhase && ACTIVE_TASK_STATUSES.has(currentRunPhase))
    || taskStatuses.some((status) => ACTIVE_TASK_STATUSES.has(status))
  ) {
    return ACTIVE_TASK_REFRESH_MS;
  }
  return IDLE_TASK_REFRESH_MS;
}
