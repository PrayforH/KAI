"use client";

import type { GroupMessage } from "../lib/assistant-client";
import { resolveDownloadUrl } from "../lib/assistant-download-url";
import { groupTimeLabel } from "../lib/group-chat-time";
import { AssistantGroupMarkdown } from "./assistant-group-markdown";
import { MESSAGE_REACTIONS, MessageActions } from "./message-actions";
import styles from "./assistant-group-message.module.css";

export const GROUP_REACTIONS = MESSAGE_REACTIONS;

const ROLE_LABEL: Record<GroupMessage["author"], string> = {
  user: "我",
  primary: "主助理",
  specialist: "子助理",
  system: "",
};

function AttachmentIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
      <path d="M12.5 6.5 7 12a2.5 2.5 0 0 0 3.5 3.5l5-5a4 4 0 0 0-5.7-5.7l-5 5a5.5 5.5 0 0 0 7.8 7.8l4-4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function statusText(status: GroupMessage["status"]): string {
  if (status === "waiting") return "等待确认后继续…";
  if (status === "running") return "正在回复…";
  if (status === "failed") return "回复失败";
  if (status === "stopped") return "回复已停止";
  return "…";
}

type AssistantGroupMessageProps = {
  message: GroupMessage;
  /** 成员主题色，用于头像与名字。 */
  tone: string;
  onReact: (message: GroupMessage, emoji: string) => void;
  onReply: (message: GroupMessage) => void;
  /** 本人消息或群创建者可以删除；运行中的消息不显示删除。 */
  canDelete?: boolean;
  onDelete?: (message: GroupMessage) => void;
};

/**
 * 一条群消息。三种形态：
 * - 成员消息：头像 + 名字，无气泡，Markdown 直接排在阅读列里；
 * - 我的消息：右对齐气泡；
 * - 系统提示：居中弱化。
 * 悬停/聚焦才露出操作条（表情、引用、复制），触屏常驻。
 */
export function AssistantGroupMessage({
  message,
  tone,
  onReact,
  onReply,
  canDelete = false,
  onDelete,
}: AssistantGroupMessageProps) {
  const mine = message.author === "user";
  const system = message.author === "system";
  const failed = message.status === "failed";
  /** 本人发送的消息不提供表情回复；表情只给助理的答复。 */
  const canReact = !mine && !system;
  const body = message.text ? (
    <AssistantGroupMarkdown text={message.text} />
  ) : (
    <span className={styles.placeholder}>{statusText(message.status)}</span>
  );
  const quote = message.replyTo ? (
    <div className={styles.quoted} aria-label={`引用 ${message.replyTo.authorName}`}>
      <span className={styles.quotedAuthor}>{message.replyTo.authorName}</span>
      <span className={styles.quotedText}>{message.replyTo.text}</span>
    </div>
  ) : null;
  const attachments = message.attachments && message.attachments.length > 0 ? (
    <ul className={styles.attachments} aria-label="附件">
      {message.attachments.map((attachment) => {
        // 服务端给的是 /v1/...，浏览器侧只代理 /api/...；缺链接时按“没有”处理，不渲染可点链接。
        const downloadUrl = resolveDownloadUrl(attachment.downloadUrl);
        return (
          <li key={attachment.documentId}>
            {downloadUrl ? (
              <a
                className={styles.attachment}
                href={downloadUrl}
                target="_blank"
                rel="noreferrer"
                title={`下载 ${attachment.name}`}
              >
                <AttachmentIcon />
                <span>{attachment.name}</span>
              </a>
            ) : (
              <span className={`${styles.attachment} ${styles.attachmentStatic}`} title={attachment.name}>
                <AttachmentIcon />
                <span>{attachment.name}</span>
              </span>
            )}
          </li>
        );
      })}
    </ul>
  ) : null;
  const actions = (
    <MessageActions
      reactions={canReact ? message.reactions : []}
      onReact={canReact ? (emoji) => onReact(message, emoji) : undefined}
      onReply={() => onReply(message)}
      copyText={message.text}
      canDelete={canDelete}
      onDelete={onDelete ? () => onDelete(message) : undefined}
      className={styles.actions}
      reactionsClassName={styles.reactionRow}
    />
  );

  if (system) {
    return (
      <article className={styles.systemRow} data-author="system">
        <div className={`${styles.systemPill} ${failed ? styles.failed : ""}`}>{body}</div>
      </article>
    );
  }

  if (mine) {
    return (
      <article className={styles.userRow} data-author="user">
        <div className={styles.userColumn}>
          <div className={styles.userMeta}>
            <span>{groupTimeLabel(message.createdAt)}</span>
          </div>
          {quote}
          <div className={`${styles.userBubble} ${failed ? styles.failedBubble : ""}`} data-message-body="true">{body}</div>
          {attachments}
          {actions}
        </div>
      </article>
    );
  }

  return (
    <article className={styles.memberRow} data-author={message.author}>
      <span className={styles.avatar} style={{ background: tone }} aria-hidden="true">
        {message.authorName.slice(0, 1)}
      </span>
      <div className={styles.memberColumn}>
        <div className={styles.memberMeta}>
          <strong style={{ color: tone }}>{message.authorName}</strong>
          <span className={styles.badge}>{ROLE_LABEL[message.author]}</span>
          <span>{groupTimeLabel(message.createdAt)}</span>
        </div>
        {quote}
        <div className={`${styles.memberBody} ${failed ? styles.failed : ""}`} data-message-body="true">{body}</div>
        {attachments}
        {actions}
      </div>
    </article>
  );
}
