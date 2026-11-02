"use client";

import { useEffect } from "react";
import { handoverStore, handoversForMessage, isLatestHandover, statusLead, useHandoverNotices } from "../lib/handover-store";
import { AssistantEventLine } from "./assistant-event-line";

/** Assistant entry can also host an ordinary task; receipts stay on main chat. */
export function shouldShowHandoverRows(personalAssistant: boolean, assistantSurface: boolean, hasBot: boolean): boolean {
  return personalAssistant && assistantSurface && !hasBot;
}

/** Delivery receipts belong to their source message, without mirrored Bot forms. */
export function HandoverInlineRows({messageId, liveRunId}: {messageId: string; liveRunId?: string}) {
  const allNotices = useHandoverNotices();
  const notices = handoversForMessage(allNotices, messageId, liveRunId);
  useEffect(() => { void handoverStore.seed(); }, []);
  if (!notices.length) return null;
  return <div className="handover-inline" aria-label="转交状态">
    {notices.map(notice => <div className="handover-inline-item" key={notice.handoverId}>
      <AssistantEventLine
        label={statusLead(notice.status)}
        name={notice.specialistName}
        botId={notice.specialistId}
        color={notice.appearance?.color}
        avatar={notice.appearance?.avatar}
        href={`/assistant?bot=${encodeURIComponent(notice.specialistId)}`}
        attention={notice.status === "failed"}
      />
      {notice.status === "pending_input" && isLatestHandover(notice, allNotices) && <p className="handover-receipt-note">等待补充信息，请在该会话继续。</p>}
      {notice.status === "failed" && <p className="handover-receipt-note" role="alert">转交未完成，请在该会话查看原因。</p>}
    </div>)}
  </div>;
}
