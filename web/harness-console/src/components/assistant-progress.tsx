"use client";

import { useEffect, useState } from "react";
import type { RunActivity } from "../lib/activity-schema";
import { modelWaitingSince, progressPhrase, shouldShowChatActivityRow } from "../lib/assistant-progress";
import { ChatActivitySteps, GrokActivityRow } from "./grok-activity";

/** One projection for both pre-response and in-turn progress; no extra model calls. */
export function AssistantProgress({ activity, avatar }: { activity: RunActivity; avatar?: string | null }) {
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
  const phrase = progressPhrase(activity);
  return <>
    <GrokActivityRow phrase={phrase} state="working" avatar={avatar} />
    <ChatActivitySteps activity={activity} phrase={phrase} />
    {since && elapsed >= 30 && <p className="chat-progress-wait" aria-live="off">
      模型尚未返回新的进展 · 已等待 {elapsed < 60 ? `${elapsed} 秒` : `${Math.floor(elapsed / 60)} 分 ${elapsed % 60} 秒`}
    </p>}
  </>;
}
