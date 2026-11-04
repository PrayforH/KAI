"use client";

import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useRunActivity, useRunViewModel } from "../lib/activity-store";
import { assistantChatReplies } from "../lib/assistant-chat";
import { useAssistantBot, useAssistantSurface } from "./assistant-bot-context";
import { AssistantProgress } from "./assistant-progress";
import { AssistantEventLine } from "./assistant-event-line";
import { handoverStore, mentionsForThread, statusLead, useHandoverNotices } from "../lib/handover-store";

/** The live status has one owner, below every newly sent message. */
export function AssistantConversationTail({ children, threadId }: { children: ReactNode; threadId?: string }) {
  const assistant = useAssistantSurface();
  const activity = useRunActivity();
  const view = useRunViewModel();
  const avatar = useAssistantBot()?.appearance?.avatar;
  const holder = useRef<HTMLSpanElement>(null);
  const [target, setTarget] = useState<HTMLElement | null>(null);
  useLayoutEffect(() => {
    setTarget(assistant ? holder.current?.closest(".harness-thread-frame")
      ?.querySelector<HTMLElement>("[data-assistant-message-tail]") ?? null : null);
  }, [assistant]);
  if (!assistant) return <>{children}</>;
  const content = <>
  {threadId ? <AssistantMentionRows threadId={threadId} /> : null}
  {children}{activity && activity.run_id === view?.runId && (
    <div className="assistant-conversation-tail" data-live-progress-for={activity.run_id}>
      <AssistantProgress activity={activity} avatar={avatar} compact
        publicMessagesVisible={assistantChatReplies(activity).length > 0} />
    </div>
  )}</>;
  return <><span hidden ref={holder} />{target && createPortal(content, target)}</>;
}

/** 用户 @ 某个助理发出的消息，展示在「发送它的」对话区：气泡 + 回执行。
 *  收据按 sourceThreadId 归属，所以换到别的助理会话里不会串台。 */
function AssistantMentionRows({ threadId }: { threadId: string }) {
  const notices = useHandoverNotices();
  const owned = mentionsForThread(notices, threadId);
  if (!owned.length) return null;
  return <div className="assistant-mention-rows" aria-label="已发送给助理的消息">
    {owned.map(receipt => <div className="assistant-mention-row" key={receipt.handoverId}>
      {receipt.text && <p className="handover-mention-text" data-mention-text="true">{receipt.text}</p>}
      <AssistantEventLine
        label={statusLead(receipt.status)} name={receipt.specialistName}
        botId={receipt.specialistId} color={receipt.appearance?.color} avatar={receipt.appearance?.avatar}
        href={`/assistant?bot=${encodeURIComponent(receipt.specialistId)}`}
        attention={receipt.status === "failed"}
      />
    </div>)}
  </div>;
}
