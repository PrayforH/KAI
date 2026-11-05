import type { RunActivity } from "./activity-schema";
import { conversationInputDisplay, conversationInputIntro } from "./conversation-input";
import { isResponseBoundary } from "./process-boundary";

export type ChatReply = { id: string; text: string; timestamp?: string; streaming: boolean };
const active = new Set(["queued", "provisioning", "running", "waiting_approval", "cancelling"]);

/** Project public messages, not transport chunks, from durable events.
 * Tool hooks may arrive between two deltas of the same provider message.
 * Only streams without an identity need the legacy tool-boundary fallback. */
export function assistantChatReplies(activity?: RunActivity): ChatReply[] {
  if (!activity) return [];
  const replies: ChatReply[] = [];
  let current: (ChatReply & { sourceId: string | undefined }) | undefined;
  for (const item of [...activity.items].sort((a, b) => a.sequence - b.sequence)) {
    const sourceId = typeof item.metadata.message_id === "string" && item.metadata.message_id.trim()
      ? item.metadata.message_id : undefined;
    if (item.event_type === "message.start" ||
      (item.event_type === "message.completed" && (!sourceId || !current?.sourceId || current.sourceId === sourceId))) {
      if (current) current.streaming = false;
      current = undefined;
    } else if (isResponseBoundary(item.event_type)) {
      if (current) current.streaming = false;
      if (!current?.sourceId) current = undefined;
    }
    if (item.event_type !== "message.delta" || !item.summary) continue;
    if (!current || current.sourceId !== sourceId) {
      if (current) current.streaming = false;
      current = { id: item.id, sourceId, text: "", timestamp: item.timestamp, streaming: active.has(activity.status) };
      replies.push(current);
    }
    current.text += item.summary;
    current.streaming = active.has(activity.status);
  }
  return replies.filter(reply => reply.text.trim()).map(({ id, text, timestamp, streaming }) => ({ id, text, timestamp, streaming }));
}

export type ChatMessage = ChatReply & { role: "assistant" | "user"; receipt?: "accepted" | "unknown" };

/** Merge durable corrections at their original send position, not at receipt time. */
export function assistantChatMessages(activity?: RunActivity): ChatMessage[] {
  if (!activity) return [];
  const positions = new Map(activity.items.map(item => [item.id, item.sequence]));
  const replies = assistantChatReplies(activity).map(reply => ({
    ...reply, role: "assistant" as const, sequence: positions.get(reply.id) ?? 0,
  }));
  const guidance = new Map<string, ChatMessage & { sequence: number }>();
  const requested = new Map<string, { sequence: number; text: string; timestamp: string }>();
  for (const item of [...activity.items].sort((a, b) => a.sequence - b.sequence)) {
    const id = item.metadata.request_id;
    if (typeof id !== "string") continue;
    if (item.event_type === "run.steer.requested" && item.summary && !requested.has(id)) {
      requested.set(id, { sequence: item.sequence, text: item.summary, timestamp: item.timestamp });
    }
    if (item.event_type === "run.steer.accepted" || item.event_type === "run.steer.unknown") {
      const original = requested.get(id);
      const text = original?.text || item.summary;
      if (text) guidance.set(id, {
        id: `guidance:${id}`, role: "user", text, streaming: false,
        timestamp: original?.timestamp ?? item.timestamp,
        sequence: original?.sequence ?? item.sequence,
        receipt: item.event_type === "run.steer.accepted" ? "accepted" : "unknown",
      });
    }
  }
  return [...replies, ...guidance.values()].sort((a, b) => a.sequence - b.sequence)
    .map(({ sequence: _, ...message }) => message);
}

export function chatTimeLabel(timestamp: string, now = new Date()): string | null {
  const date = new Date(timestamp);
  if (!Number.isFinite(date.getTime())) return null;
  const day = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today); yesterday.setDate(today.getDate() - 1);
  const prefix = day.getTime() === today.getTime() ? "今天" : day.getTime() === yesterday.getTime() ? "昨天" :
    `${date.getFullYear() !== now.getFullYear() ? `${date.getFullYear()}年` : ""}${date.getMonth() + 1}月${date.getDate()}日周${"日一二三四五六"[date.getDay()]}`;
  return `${prefix} ${date.getHours()}:${String(date.getMinutes()).padStart(2, "0")}`;
}

export function showChatTime(timestamp?: string, previous?: string, now = Date.now()): boolean {
  if (!timestamp || !Number.isFinite(Date.parse(timestamp))) return false;
  // Fresh sends belong to the live exchange. Only settled history gets separators;
  // do not stamp every newly mounted response or an optimistic history hand-off.
  if (now - Date.parse(timestamp) < 5 * 60_000) return false;
  if (!previous || !Number.isFinite(Date.parse(previous))) return true;
  const current = new Date(timestamp), before = new Date(previous);
  return current.toDateString() !== before.toDateString() || current.getTime() - before.getTime() >= 5 * 60_000;
}

/** Reserved form/UI payloads stay in their own existing components while streaming. */
export function publicChatText(text: string): string {
  return conversationInputDisplay(conversationInputIntro(text), true)
    .replace(/(?:^|\n)```harness-ui(?:\s|$)[\s\S]*?(?:```|$)/g, "").trim();
}
