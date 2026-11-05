"use client";
import { useAuiState } from "@assistant-ui/react";
import { runActivitySchema, type RunActivity } from "../lib/activity-schema";
import { noticeSource } from "./assistant-notice-source";
import { chatTimeLabel, showChatTime } from "../lib/assistant-chat";

type Part = { type: string; toolName?: string; args?: Record<string, unknown> };
export function messageRunTime(parts: readonly Part[]): string | undefined {
  const part = parts.find(part => part.type === "tool-call" && part.toolName === "harness_run_activity");
  const result = runActivitySchema.safeParse(part?.args?.activity);
  return result.success ? result.data.started_at : noticeSource(parts)?.timestamp;
}

/** Historical createdAt defaults to reload time. Never display it as sent time. */
export function AssistantChatTime({ activity }: { activity?: RunActivity }) {
  const previous = useAuiState(state => {
    const index = state.thread.messages.findIndex(message => message.id === state.message.id);
    for (let i = index - 1; i >= 0; i--) {
      const time = messageRunTime(state.thread.messages[i].content as readonly Part[]);
      if (time) return time;
    }
    return undefined;
  });
  const contentTime = useAuiState(state => messageRunTime(state.message.content as readonly Part[]));
  const timestamp = activity?.started_at ?? contentTime;
  if (!showChatTime(timestamp, previous)) return null;
  const label = chatTimeLabel(timestamp!);
  return label ? <div className="assistant-chat-time"><time dateTime={timestamp}>{label}</time></div> : null;
}
