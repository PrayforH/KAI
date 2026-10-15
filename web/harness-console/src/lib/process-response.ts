import type { RunActivity } from "./activity-schema";
import { isResponseBoundary } from "./process-boundary";

const normalized = (text: string) => text.replace(/\s+/g, " ").trim();

/** Both slots decide ownership from the same activity projection. A store
 * notification can render before hideForTool updates the live response store. */
export function responseTextOutsideProcess(text: string, activity?: RunActivity): string {
  if (!text.trim() || !activity) return text;
  const items = [...activity.items].sort((a,b) => a.sequence - b.sequence);
  const boundaries = items.filter(item => isResponseBoundary(item.event_type)).map(item => item.sequence);
  const lastBoundary = boundaries.at(-1);
  if (lastBoundary === undefined) return text;
  const messages = items.filter(item => item.event_type === "message.delta" && item.summary);
  // Preserve an authoritative post-tool repetition, while hiding a stale
  // cached preface even if a different new answer has already started.
  const trailing = normalized(messages.filter(item => item.sequence > lastBoundary).map(item => item.summary).join(""));
  const blocks = new Map<number,string>();
  for (const item of messages) {
    const next = boundaries.find(sequence => sequence > item.sequence);
    if (next !== undefined) blocks.set(next, (blocks.get(next) ?? "") + item.summary);
  }
  const candidate = normalized(text);
  const progress = [...blocks.values()].map(normalized);
  const inProcess = progress.some(block => block === candidate) || normalized([...blocks.values()].join("\n")) === candidate;
  return inProcess && trailing !== candidate ? "" : text;
}

export function hasPendingProcessProse(activity: RunActivity): boolean {
  return ["queued", "running", "waiting_approval"].includes(activity.status) &&
    !activity.items.some(item => ["run.succeeded", "run.cancelled", "run.failed", "run.rejected", "run.timed_out"].includes(item.event_type));
}

/** Unclassified live prose belongs to the process until the run settles. */
export function responseTextForDisplay(text: string, activity?: RunActivity): string {
  if (activity && hasPendingProcessProse(activity)) return "";
  return responseTextOutsideProcess(text, activity);
}
