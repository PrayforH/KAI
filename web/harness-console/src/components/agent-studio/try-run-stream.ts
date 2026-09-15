import type { StudioTryRun } from "../../lib/studio-client";

export type StudioTryRunEvent = StudioTryRun["events"][number];

const ACTION_BOUNDARY_PREFIXES = ["approval.", "subagent.", "tool."] as const;
const TERMINAL_STATUS_BY_EVENT: Record<string, StudioTryRun["run"]["status"]> = {
  "run.queued": "queued",
  "run.provisioning": "provisioning",
  "run.running": "running",
  "run.waiting_approval": "waiting_approval",
  "run.cancelling": "cancelling",
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
  if (event.type === "studio.snapshot") {
    const snapshot = event.payload as Partial<StudioTryRun>;
    if (snapshot.run?.run_id !== current.run.run_id) return current;
    return {
      ...current, activity: snapshot.activity ?? current.activity,
      approvals: snapshot.approvals ?? current.approvals,
      artifacts: snapshot.artifacts ?? current.artifacts,
      run: event.sequence >= (current.events.at(-1)?.sequence ?? 0) ? snapshot.run : current.run,
    };
  }
  if (current.events.some((candidate) => candidate.sequence === event.sequence)) return current;
  const events = [...current.events, event].sort((left, right) => left.sequence - right.sequence);
  const latest = events.at(-1)!;
  const status = event.sequence === latest.sequence
    ? TERMINAL_STATUS_BY_EVENT[event.type] ?? (event.type === "approval.requested" ? "waiting_approval" : current.run.status)
    : current.run.status;
  const approvals = new Map(current.approvals.map(item => [item.approval_id, item]));
  for (const item of events) {
    const id = item.payload.approval_id;
    if (typeof id !== "string") continue;
    if (item.type === "approval.requested" && !approvals.has(id)) {
      approvals.set(id, {
        approval_id: id, status: "pending",
        tool_name: typeof item.payload.tool_name === "string" ? item.payload.tool_name : null,
        reason: typeof item.payload.reason === "string" ? item.payload.reason : "此操作需要确认",
        argument_summary: (item.payload.argument_summary ?? {}) as Record<string, unknown>,
        risk: typeof item.payload.risk === "string" ? item.payload.risk : null,
        expires_at: typeof item.payload.expires_at === "string" ? item.payload.expires_at : undefined,
      });
    } else if (["approval.approved", "approval.rejected", "approval.expired", "approval.cancelled"].includes(item.type)) {
      const approval = approvals.get(id);
      if (approval) approvals.set(id, { ...approval, status: item.type.split(".")[1] as typeof approval.status });
    }
  }
  return {
    ...current,
    run: status === current.run.status ? current.run : { ...current.run, status },
    events,
    approvals: [...approvals.values()],
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
        approvals: current.approvals,
        finalText: current.finalText,
      };
}
