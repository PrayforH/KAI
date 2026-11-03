"use client";

import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { useRunActivity, useRunViewModel } from "../lib/activity-store";
import { assistantChatReplies } from "../lib/assistant-chat";
import { useAssistantBot, useAssistantSurface } from "./assistant-bot-context";
import { AssistantProgress } from "./assistant-progress";

/** The live status has one owner, below every newly sent message. */
export function AssistantConversationTail({ children }: { children: ReactNode }) {
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
  {children}{activity && activity.run_id === view?.runId && (
    <div className="assistant-conversation-tail" data-live-progress-for={activity.run_id}>
      <AssistantProgress activity={activity} avatar={avatar} compact
        publicMessagesVisible={assistantChatReplies(activity).length > 0} />
    </div>
  )}</>;
  return <><span hidden ref={holder} />{target && createPortal(content, target)}</>;
}
