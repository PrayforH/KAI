export type TaskThreadState = "unknown" | "empty" | "durable";

export type TaskLaunchIntent = "new-task" | "select-agent";

export type TaskLaunchMode =
  | "focus-current"
  | "reuse-current"
  | "create-thread";

/**
 * An unsent task shell has no server-side history and therefore no way back
 * from the task list. Reuse it instead of orphaning its browser-local draft.
 * When task history is unavailable, only the non-mutating focus action is
 * allowed; selecting another Agent creates a fresh thread rather than risking
 * a rebind of an existing durable task.
 */
export function resolveTaskLaunchMode(
  state: TaskThreadState,
  intent: TaskLaunchIntent,
): TaskLaunchMode {
  if (state === "durable") return "create-thread";
  if (intent === "new-task") return "focus-current";
  return state === "empty" ? "reuse-current" : "create-thread";
}
