"use client";

import { useEffect, useMemo, useRef } from "react";
import { handoverStore, mentionsForThread, useHandoverNotices } from "../lib/handover-store";
import { MENTION_RESULT_EVENT, watchMentionCompletion } from "../lib/mention-completion";
import { AssistantEventLine } from "./assistant-event-line";
/**
 * 已发送消息给 X 的回执行：居中、带头像与名称，点了直接去那个 Bot 的会话。
 * 数据来自持久 handover（刷新后仍在），不是本地临时状态。
 */
export function AssistantMentionReceipts({ threadId, limit = 3 }: { threadId: string; limit?: number }) {
  const notices = useHandoverNotices();
  useEffect(() => { void handoverStore.seed(); }, []);
  const owned = useMemo(() => mentionsForThread(notices, threadId), [notices, threadId]);
  const completed = useRef(new Set<string>());
  useEffect(() => {
    const controller = new AbortController();
    for (const receipt of owned) {
      if (!receipt.runId || completed.current.has(receipt.runId)) continue;
      const runId = receipt.runId;
      void watchMentionCompletion(runId, controller.signal).then(terminal => {
        if (!terminal || controller.signal.aborted) return;
        completed.current.add(runId);
        window.dispatchEvent(new CustomEvent(MENTION_RESULT_EVENT, { detail: { threadId } }));
      }).catch(() => { /* History remains the recovery source after reconnect/refresh. */ });
    }
    return () => controller.abort();
  }, [owned, threadId]);
  const mentions = owned.slice(-limit);
  if (!mentions.length) return null;
  return (
    <div className="assistant-mention-receipts" aria-label="已发送的消息">
      {mentions.map((notice) => (
        <AssistantEventLine
          key={notice.handoverId}
          label="已发送消息给"
          name={notice.specialistName}
          botId={notice.specialistId}
          color={notice.appearance?.color}
          avatar={notice.appearance?.avatar}
          href={`/assistant?bot=${encodeURIComponent(notice.specialistId)}`}
        />
      ))}
    </div>
  );
}
