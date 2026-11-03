"use client";

import { useHandoverNotices } from "../lib/handover-store";
import { AssistantEventLine } from "./assistant-event-line";
/**
 * 已发送消息给 X 的回执行：居中、带头像与名称，点了直接去那个 Bot 的会话。
 * 数据来自持久 handover（刷新后仍在），不是本地临时状态。
 */
export function AssistantMentionReceipts({ limit = 3 }: { limit?: number }) {
  const notices = useHandoverNotices();
  const mentions = notices.filter((notice) => notice.mention).slice(-limit);
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
