"use client";

import { useHandoverNotices } from "../lib/handover-store";
import { AssistantEventLine } from "./assistant-event-line";
import type { MentionedAssistant } from "../lib/bot-mentions";

/** 已提及的助理：选中即成形（稳定 ID），发送前可以逐个删掉。 */
export function MentionChips({
  mentions,
  onRemove,
}: {
  mentions: readonly MentionedAssistant[];
  onRemove: (specialistId: string) => void;
}) {
  if (!mentions.length) return null;
  return (
    <div className="assistant-mention-chips" aria-label="已提及的助理">
      {mentions.map((item) => (
        <span className="assistant-mention-chip" key={item.specialistId}>
          <span>@{item.name}</span>
          <button
            type="button"
            aria-label={`移除 @${item.name}`}
            onClick={() => onRemove(item.specialistId)}
          >
            ×
          </button>
        </span>
      ))}
    </div>
  );
}

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
