"use client";

import { useEffect, useRef, useState } from "react";

import { listAssistantGroupMessages, type GroupMessage } from "../lib/assistant-client";
import { GROUP_MESSAGE_STATUS_LABEL } from "../lib/group-message-status";
import { readableGroupMessageText } from "../lib/group-message-text";
import { groupTimeLabel } from "../lib/group-chat-time";
import { ChatHistoryList } from "./chat-history-list";
import styles from "./assistant-history-panel.module.css";

/** 群聊天记录刷新间隔：与群消息轮询同量级，打开期间保持列表跟手。 */
const REFRESH_MS = 5000;

type AssistantGroupHistoryPanelProps = {
  open: boolean;
  groupId: string;
  onClose: () => void;
};

/**
 * 群聊的聊天记录列：和单助理共用同一条历史入口（外侧按钮开一格历史列），
 * 数据仍走群级的 messages 接口，不另造 thread。
 *
 * 「在聊天中定位」通过 `kai:group-locate` 事件交给群会话滚动并高亮目标消息，
 * 面板本身不持有消息滚动区。
 */
export function AssistantGroupHistoryPanel({ open, groupId, onClose }: AssistantGroupHistoryPanelProps) {
  const [messages, setMessages] = useState<GroupMessage[] | null>(null);
  const [error, setError] = useState("");
  const closeButton = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setMessages(null);
    setError("");
    const load = async () => {
      try {
        const list = await listAssistantGroupMessages(groupId);
        if (cancelled) return;
        setMessages(list);
        setError("");
      } catch (cause) {
        if (cancelled) return;
        setError(cause instanceof Error && cause.message ? cause.message : "聊天记录暂时无法读取");
      }
    };
    void load();
    const timer = window.setInterval(() => void load(), REFRESH_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [open, groupId]);

  useEffect(() => {
    if (!open) return;
    closeButton.current?.focus();
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
  }, [open, onClose]);

  if (!open) return null;

  const items = (messages ?? []).map((message) => ({
    id: message.messageId,
    author: message.authorName,
    // 状态载荷渲染成可读状态，认不出来的按空正文处理；用户输入保持原样。
    text: (message.author === "user" ? message.text : readableGroupMessageText(message.text))
      || GROUP_MESSAGE_STATUS_LABEL[message.status],
    time: groupTimeLabel(message.createdAt),
  }));

  return <aside className={styles.drawer} aria-label="群聊天记录">
    <header className={styles.head}>
      <strong>聊天记录</strong>
      <button ref={closeButton} type="button" className={styles.close} aria-label="关闭聊天记录" onClick={onClose}>×</button>
    </header>
    <div className={styles.body}>
      <ChatHistoryList
        items={items}
        loading={messages === null && !error}
        error={error}
        emptyText="这个群还没有聊天记录"
        searchPlaceholder="搜索群聊记录"
        label="群聊天记录"
        onLocate={(messageId) => {
          window.dispatchEvent(new CustomEvent("kai:group-locate", {
            detail: { groupId, messageId },
          }));
        }}
      />
    </div>
  </aside>;
}
