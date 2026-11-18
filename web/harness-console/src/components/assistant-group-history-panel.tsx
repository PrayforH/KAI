"use client";

import { useEffect, useState } from "react";

import { listAssistantGroupMessages, type GroupMessage, type GroupMessageAttachment } from "../lib/assistant-client";
import { resolveDownloadUrl } from "../lib/assistant-download-url";
import { GROUP_MESSAGE_STATUS_LABEL } from "../lib/group-message-status";
import { readableGroupMessageText } from "../lib/group-message-text";
import type { HistoryEntry, HistoryFile } from "../lib/history-files";
import { HistoryDrawer } from "./history-drawer";

/** 群聊天记录刷新间隔：与群消息轮询同量级，打开期间保持列表跟手。 */
const REFRESH_MS = 5000;

type AssistantGroupHistoryPanelProps = {
  open: boolean;
  groupId: string;
  onClose: () => void;
};

/** 群消息附件 → 统一的历史文件；群侧没有 artifact id，打开走资料库内容链接。 */
function groupAttachmentHref(attachment: GroupMessageAttachment): string | null {
  const base = resolveDownloadUrl(attachment.downloadUrl);
  if (!base) return null;
  const isImage = (attachment.contentType ?? "").toLowerCase().startsWith("image/");
  if (!isImage) return base;
  // 图片缩略图与放大都用行内响应；服务端只对 image/text/pdf 放行 inline。
  return `${base}${base.includes("?") ? "&" : "?"}inline=1`;
}

/** 群消息按时间倒序（最新在上）转成历史记录，和单助理共用同一套文件分类。 */
export function groupHistoryEntries(messages: GroupMessage[]): { entries: HistoryEntry[]; files: HistoryFile[] } {
  // 时间相同（同一轮的提问与回复）按接口顺序倒过来，保证最新一条仍在最上。
  const ordered = messages
    .map((message, index) => ({ message, index }))
    .sort((a, b) => (Date.parse(b.message.createdAt) - Date.parse(a.message.createdAt)) || (b.index - a.index))
    .map(item => item.message);
  const entries: HistoryEntry[] = [];
  for (const message of ordered) {
    const files: HistoryFile[] = (message.attachments ?? []).map(attachment => ({
      id: `group-doc:${attachment.documentId}`,
      name: attachment.name,
      mediaType: attachment.contentType ?? "",
      sizeBytes: attachment.sizeBytes ?? null,
      createdAt: message.createdAt,
      origin: message.author === "user" ? "user" as const : "assistant" as const,
      messageId: message.messageId,
      artifactId: null,
      inlineHref: null,
      downloadHref: groupAttachmentHref(attachment),
    }));
    // 状态载荷渲染成可读状态，认不出来的按空正文处理；用户输入保持原样。
    const text = (message.author === "user" ? message.text : readableGroupMessageText(message.text))
      || GROUP_MESSAGE_STATUS_LABEL[message.status];
    if (!text.trim() && files.length === 0) continue;
    entries.push({
      id: message.messageId,
      role: message.author === "user" ? "user" : "assistant",
      text,
      files,
      label: message.author === "user" ? "我" : message.authorName,
    });
  }
  const files = entries.flatMap(entry => entry.files);
  return { entries, files };
}

/**
 * 群聊的聊天记录列：和单助理共用同一条历史入口（外侧按钮开一格历史列）、
 * 同一套 tabs / 搜索 / 筛选 / 文件分类，数据仍走群级的 messages 接口，不另造 thread。
 *
 * 「在聊天中定位」通过 `kai:group-locate` 事件交给群会话滚动并高亮目标消息，
 * 面板本身不持有消息滚动区。
 */
export function AssistantGroupHistoryPanel({ open, groupId, onClose }: AssistantGroupHistoryPanelProps) {
  const [messages, setMessages] = useState<GroupMessage[] | null>(null);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);

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
  }, [open, groupId, reload]);

  if (!open) return null;

  const { entries, files } = groupHistoryEntries(messages ?? []);

  return <HistoryDrawer
    ariaLabel="群聊天记录"
    assistantName="助理"
    status={error ? "error" : messages === null ? "loading" : "ready"}
    error={error}
    entries={entries}
    files={files}
    emptyEntriesText="这个群还没有聊天记录"
    searchPlaceholder="搜索群聊记录"
    hrefFor={file => file.downloadHref}
    onLocate={messageId => {
      window.dispatchEvent(new CustomEvent("kai:group-locate", {
        detail: { groupId, messageId },
      }));
      return true;
    }}
    onRetry={() => setReload(value => value + 1)}
    onClose={onClose}
  />;
}
