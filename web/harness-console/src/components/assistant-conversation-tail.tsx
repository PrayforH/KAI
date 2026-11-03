"use client";

import type { ReactNode } from "react";
import type { RunActivity } from "../lib/activity-schema";
import { useRunActivity, useRunViewModel } from "../lib/activity-store";
import { assistantChatReplies } from "../lib/assistant-chat";
import { useAssistantBot, useAssistantSurface } from "./assistant-bot-context";
import { AssistantProgress } from "./assistant-progress";
import { SteeringUserMessage } from "./steering-user-message";

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

/** Receipts remain durable message facts; transport diagnostics stay out of chat. */
export function AssistantSteeringMessages({ activity }: { activity: RunActivity }) {
  const messages = new Map<string, { text: string; status: "accepted" | "unknown" }>();
  for (const item of activity.items) {
    const id = item.metadata.request_id;
    if (typeof id !== "string") continue;
    if (item.event_type === "run.steer.accepted" || item.event_type === "run.steer.unknown") {
      if (item.summary) messages.set(id, { text: item.summary, status: item.event_type === "run.steer.accepted" ? "accepted" : "unknown" });
    }
  }
  return <>{[...messages].map(([id, message]) => <SteeringUserMessage key={id} chat {...message} />)}</>;
}
