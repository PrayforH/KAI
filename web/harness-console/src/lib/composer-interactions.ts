import type { Attachment, CompleteAttachment } from "@assistant-ui/react";
import { inputArtifactIdFromAttachment } from "./input-attachment-adapter";

export const COMPOSER_COMMANDS = [
  { name: "/new", label: "新建任务", description: "当前输入会保留为原任务草稿" },
  { name: "/stop", label: "停止运行", description: "停止当前任务，并暂停后续队列" },
  { name: "/files", label: "任务文件", description: "打开右侧文件分栏" },
  { name: "/clear", label: "清空输入", description: "保留对话历史与队列" },
  { name: "/help", label: "输入帮助", description: "/ 命令 · @ 智能体 · $ 技能" },
];
export function composerTrigger(text: string, caret: number) {
  const before = text.slice(0, caret);
  const match = /(?:^|\s)([/@$])([^\s/@$]*)$/u.exec(before);
  if (!match || (match[1] === "/" && before.trimStart() !== match[0].trimStart())) return null;
  return { symbol: match[1], query: match[2].toLowerCase(), start: caret - match[2].length - 1, end: caret };
}
export interface QueuedPrompt { id: string; text: string; attachments: CompleteAttachment[]; steerRunId?: string; intent?: "steer" | "next"; receipt?: "unknown" | "failed"; historyRunId?: string }
export function queueAttachments(attachments: readonly Attachment[]): CompleteAttachment[] {
  return attachments.map((attachment) => {
    if (attachment.status.type === "complete") return attachment as CompleteAttachment;
    const id = inputArtifactIdFromAttachment(attachment);
    if (!id || attachment.status.type !== "requires-action") throw new Error(`请等待 ${attachment.name} 上传完成，或移除失败附件。`);
    const mimeType = attachment.contentType || "application/octet-stream";
    return { id, type: attachment.type, name: attachment.name, contentType: mimeType,
      status: { type: "complete" }, content: [{ type: "file", data: id, mimeType, filename: attachment.name }] };
  });
}
export function queueMayDispatch(busy: boolean, paused: boolean, phase?: string) {
  return !busy && !paused && !["running", "queued", "waiting_approval"].includes(phase ?? "");
}
export function restorePromptQueue(raw: string | null): QueuedPrompt[] {
  try {
    const value: unknown = JSON.parse(raw ?? "[]");
    if (!Array.isArray(value)) return [];
    return value.filter((item): item is QueuedPrompt => Boolean(item && typeof item.id === "string" && typeof item.text === "string" && Array.isArray(item.attachments) && item.attachments.every((a: CompleteAttachment) => a?.status?.type === "complete" && Array.isArray(a.content)))).slice(0, 50);
  } catch { return []; }
}

/** Only a confirmed non-delivery can become a fresh turn automatically. */
export function settleAssistantFollowup(item: QueuedPrompt, status: string): QueuedPrompt | null {
  if (status === "accepted") return null;
  if (status === "not_delivered") return { ...item, intent: "next", steerRunId: undefined, receipt: undefined };
  if (status === "unknown" || status === "failed") return { ...item, receipt: status };
  return item;
}

export function nextQueuedPrompt(items: QueuedPrompt[]): QueuedPrompt | undefined {
  // Ambiguous writes stay retained; they do not block later, distinct messages.
  return items.find(item => !item.receipt && !item.steerRunId);
}


export function nextSteeringPrompt(items: QueuedPrompt[], awaitingReceipt: readonly string[]): QueuedPrompt | undefined {
  return items.find(item => item.intent === "steer" && !item.steerRunId && !item.receipt && !item.attachments.length && !awaitingReceipt.includes(item.id));
}


/** Reconcile legacy local rows against owned, already loaded durable history. */
export function reconcilePromptHistory(items: QueuedPrompt[], activities: ReadonlyArray<{run_id: string; status: string; items: ReadonlyArray<{event_type: string; metadata: Record<string, unknown>}>}>): QueuedPrompt[] {
  const receipts = new Map<string, {runId: string; status: string; terminal: boolean}>();
  for (const activity of activities) for (const event of activity.items) {
    if (!event.event_type.startsWith("run.steer.")) continue;
    const id = event.metadata.request_id;
    if (typeof id === "string") receipts.set(id, {runId: activity.run_id, status: event.event_type, terminal: !["running", "queued", "waiting_approval", "provisioning"].includes(activity.status)});
  }
  let changed = false;
  const next = items.flatMap(item => {
    if (!item.receipt) return [item];
    const receipt = receipts.get(item.id);
    if (!receipt) return [item];
    if (["run.steer.accepted", "run.steer.unknown"].includes(receipt.status)) { changed = true; return []; }
    if (receipt.terminal && item.historyRunId !== receipt.runId) {
      changed = true;
      return [{ ...item, historyRunId: receipt.runId }];
    }
    return [item];
  });
  return changed ? next : items;
}
