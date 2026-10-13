"use client";

/** Local Codex UserMessage: an end-aligned compact message, independent of
 * the surrounding collapsible agent activity. The receipt remains accessible
 * without adding a banner to the authored message. */
export function SteeringUserMessage({ text, status }: { text: string; status: "pending" | "accepted" }) {
  return <article className="steering-user-message" data-steering-status={status}
    aria-label={status === "accepted" ? "已接收的补充说明" : "等待接收的补充说明"}>
    <div className="steering-user-message-bubble">{text}</div>
    {status === "pending" && <small className="steering-user-message-status" role="status">等待接收…</small>}
  </article>;
}
