import type { Specialist } from "./assistant-client";
import type { RunViewModel } from "./run-view-model";

const WORKING = new Set(["queued", "provisioning", "running", "cancelling"]);

/** Reuse the sidebar's owned thread summaries; Bot availability is not work. */
export function botIsWorking(activity: Specialist["activity"], currentThreadId?: string, live?: RunViewModel): boolean {
  if (!activity) return false;
  const phase = live && activity.threadId === currentThreadId && activity.runId === live.runId
    ? live.phase : activity.status;
  return WORKING.has(phase);
}
