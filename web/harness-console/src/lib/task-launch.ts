export type TaskThreadState = "unknown" | "empty" | "durable";

export type TaskLaunchIntent = "new-task" | "select-agent";

export type TaskLaunchMode =
  | "reuse-current"
  | "create-thread";

/** Explicit New Task actions always create a fresh conversation, even while
 * the previous task's history is still loading. Agent selection can reuse an
 * unsent shell without changing the user's draft. */
export function resolveTaskLaunchMode(
  state: TaskThreadState,
  intent: TaskLaunchIntent,
): TaskLaunchMode {
  if (intent === "new-task" || state === "durable") return "create-thread";
  return state === "empty" ? "reuse-current" : "create-thread";
}
