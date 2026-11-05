"use client";
import { useAssistantSurface } from "./assistant-bot-context";
import { useConversationRedactor } from "./conversation-redaction";

const RECEIPT_COPY = {
  pending: { label: "等待接收的补充说明", detail: "等待接收…" },
  accepted: { label: "已接收的补充说明", detail: "" },
  unknown: {
    label: "接收状态待核对的补充说明",
    detail: "接收状态待核对，请查看回复后决定是否重发。",
  },
};

/** Local Codex UserMessage: an end-aligned compact message, independent of
 * the surrounding collapsible agent activity. The receipt remains accessible
 * without adding a banner to the authored message. */
export function SteeringUserMessage({ text, status, chat = false }: { text: string; status: "pending" | "accepted" | "unknown"; chat?: boolean }) {
  const assistantSurface = useAssistantSurface();
  const redact = useConversationRedactor();
  const copy = RECEIPT_COPY[status];
  return <article className="steering-user-message" data-steering-status={status}
    aria-label={copy.label}>
    <div className="steering-user-message-bubble">{redact(text)}</div>
    {!chat && !assistantSurface && copy.detail && <small className="steering-user-message-status" role="status">{copy.detail}</small>}
  </article>;
}
