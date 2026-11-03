"use client";

import type { ReactNode } from "react";
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
  return <>{children}{assistant && activity && activity.run_id === view?.runId && (
    <div className="assistant-conversation-tail" data-live-progress-for={activity.run_id}>
      <AssistantProgress activity={activity} avatar={avatar} compact
        publicMessagesVisible={assistantChatReplies(activity).length > 0} />
    </div>
  )}</>;
}
