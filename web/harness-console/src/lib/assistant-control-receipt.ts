import { z } from "zod";
import type { RunActivity } from "./activity-schema";

const receiptSchema = z.object({
  kind: z.literal("work_notifications"),
  objective_id: z.string().min(1).max(128),
  revision: z.number().int().min(1),
  action: z.enum(["query", "update"]),
  summary: z.string().min(1).max(300),
}).strict();

const routineTools = new Set([
  "assistant_resolve_target", "assistant_work_notifications",
].map((name) => `mcp__harness-builder__${name}`));

/** Same durable Run activity feeds live replies and history. No prose is rewritten. */
export function assistantControlSummary(activity: RunActivity | undefined): string | null {
  if (!activity || !["running", "succeeded"].includes(activity.status)) return null;
  if (activity.items.some((item) => item.kind === "error" || item.kind === "artifact" ||
    item.kind === "subagent" || ["failed", "rejected", "timed_out", "cancelled", "waiting"].includes(item.status))) return null;
  const requests = activity.items.filter((item) => item.event_type === "tool.request");
  if (!requests.length || requests.some((item) => !routineTools.has(String(item.metadata.name)))) return null;
  const latest = activity.items.filter((item) => item.event_type === "ui.assistant_control")
    .reduce<RunActivity["items"][number] | undefined>((last, item) =>
      !last || item.sequence > last.sequence ? item : last, undefined);
  // A new request after a receipt may still fail or perform unrelated work.
  if (!latest || latest.kind !== "result" || latest.status !== "succeeded" ||
    requests.some((item) => item.sequence > latest.sequence)) return null;
  const receipt = receiptSchema.safeParse(latest.metadata.assistant_control);
  return receipt.success ? receipt.data.summary : null;
}
