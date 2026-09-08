import type { StudioTryRun } from "../../lib/studio-client";

export type StudioTryRunEvent = StudioTryRun["events"][number];

const ACTION_BOUNDARY_PREFIXES = ["approval.", "subagent.", "tool."] as const;
const TERMINAL_STATUS_BY_EVENT: Record<string, StudioTryRun["run"]["status"]> = {
  "run.cancelled": "cancelled",
  "run.failed": "failed",
  "run.rejected": "rejected",
  "run.succeeded": "succeeded",
  "run.timed_out": "timed_out",
};

function eventText(event: StudioTryRunEvent): string {
  return typeof event.payload.text === "string" ? event.payload.text : "";
}

function isActionBoundary(event: StudioTryRunEvent): boolean {
  return ACTION_BOUNDARY_PREFIXES.some((prefix) => event.type.startsWith(prefix));
}

export function projectTryRunConversation(events: readonly StudioTryRunEvent[]) {
  const lastActionSequence = events.reduce(
    (latest, event) => isActionBoundary(event) ? Math.max(latest, event.sequence) : latest,
    -1,
  );
  const processText = events
    .filter((event) => (
      event.type === "reasoning.summary.delta"
      || (event.type === "message.delta" && event.sequence <= lastActionSequence)
    ))
    .map(eventText)
    .join("");
  const answerText = events
    .filter((event) => event.type === "message.delta" && event.sequence > lastActionSequence)
    .map(eventText)
    .join("");
  return { processText, answerText };
}

export function appendTryRunEvent(
  current: StudioTryRun,
  event: StudioTryRunEvent,
): StudioTryRun {
  if (current.events.some((candidate) => candidate.sequence === event.sequence)) return current;
  const events = [...current.events, event].sort((left, right) => left.sequence - right.sequence);
  const status = TERMINAL_STATUS_BY_EVENT[event.type] ?? current.run.status;
  return {
    ...current,
    run: status === current.run.status ? current.run : { ...current.run, status },
    events,
    finalText: projectTryRunConversation(events).answerText,
  };
}

export function mergeTryRunView(
  current: StudioTryRun | null,
  incoming: StudioTryRun,
): StudioTryRun {
  if (!current || current.run.run_id !== incoming.run.run_id) return incoming;
  const currentLastSequence = current.events.at(-1)?.sequence ?? 0;
  const incomingLastSequence = incoming.events.at(-1)?.sequence ?? 0;
  return incomingLastSequence >= currentLastSequence
    ? incoming
    : {
        ...incoming,
        run: current.run,
        events: current.events,
        finalText: current.finalText,
      };
}
