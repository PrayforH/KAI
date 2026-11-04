import type { TaskSummary } from "./task-history";
import type { RunViewModel } from "./run-view-model";

const WORKING = new Set(["queued", "provisioning", "running", "cancelling"]);

/** Reuse the sidebar's owned thread summaries; Bot availability is not work. */
export function workingBotThreads(
  threads: readonly TaskSummary[], currentThreadId: string, live?: RunViewModel,
): ReadonlySet<string> {
  return new Set(threads.filter(thread => {
    const phase = live && thread.thread_id === currentThreadId && thread.run_id === live.runId
      ? live.phase : thread.status;
    return WORKING.has(phase);
  }).map(thread => thread.thread_id));
}
