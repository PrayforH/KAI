"use client";

import { useEffect, useState } from "react";
import type { RunActivity } from "../lib/activity-schema";
import { hasPendingProgressAction, modelWaitingSince, progressPhrase, shouldShowChatActivityRow } from "../lib/assistant-progress";
import { ChatActivitySteps, GrokActivityRow } from "./grok-activity";

/** One projection for both pre-response and in-turn progress; no extra model calls. */
export function AssistantProgress({ activity, avatar, publicMessagesVisible = false, compact = false }: { activity: RunActivity; avatar?: string | null; publicMessagesVisible?: boolean; compact?: boolean }) {
  const visible = shouldShowChatActivityRow(activity.status, activity);
  const since = visible ? modelWaitingSince(activity) : undefined;
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!since) return;
    setNow(Date.now());
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [since]);
  if (!visible) return null;
  const elapsed = since ? Math.max(0, Math.floor((now - Date.parse(since)) / 1000)) : 0;
  const process = publicMessagesVisible ? { ...activity, items: activity.items.filter(item => item.event_type !== "message.delta") } : activity;
  const phrase = progressPhrase(process);
  const textAt = activity.items.findLastIndex(item => item.event_type === "message.delta");
  const processAt = activity.items.findLastIndex(item => /^(?:tool|runtime|context|approval|subagent|artifact)\./.test(item.event_type));
  if (publicMessagesVisible && textAt > processAt && !hasPendingProgressAction(activity)) return null;
  return <>
    <GrokActivityRow phrase={phrase} state="working" avatar={avatar} />
    {!compact && !publicMessagesVisible && <ChatActivitySteps activity={activity} phrase={phrase} />}
    {!compact && since && elapsed >= 30 && <p className="chat-progress-wait" aria-live="off">
      模型尚未返回新的进展 · 已等待 {elapsed < 60 ? `${elapsed} 秒` : `${Math.floor(elapsed / 60)} 分 ${elapsed % 60} 秒`}
    </p>}
  </>;
}
