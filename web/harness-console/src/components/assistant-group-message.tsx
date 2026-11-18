"use client";

import type { GroupMessage } from "../lib/assistant-client";
import { resolveDownloadUrl } from "../lib/assistant-download-url";
import { groupTimeLabel } from "../lib/group-chat-time";
import {
  GROUP_REPLY_FALLBACK_TEXT,
  GROUP_REPLY_STAGE_LABEL,
} from "../lib/group-reply-progress";
import { useGroupReplyProgress } from "../lib/use-group-reply-progress";
import { readableGroupMessageText } from "../lib/group-message-text";
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
  if (status === "running") return GROUP_REPLY_FALLBACK_TEXT;
  if (status === "failed") return "回复失败";
  if (status === "stopped") return "回复已停止";
  return "…";
}

/**
 * 运行中的回复：跟随既有 run 事件流显示「正在思考 / 正在调用工具 / 正在整理回复」。
 * 流拿不到时退回「正在回复…」，由消息轮询负责最终结果。
 */
function RunningReplyStatus({ runId }: { runId: string }) {
  const stage = useGroupReplyProgress(runId, true);
  return (
    <span className={`${styles.placeholder} ${styles.running}`} role="status" aria-live="polite">
      {stage ? GROUP_REPLY_STAGE_LABEL[stage] : GROUP_REPLY_FALLBACK_TEXT}
    </span>
  );
}

type AssistantGroupMessageProps = {
  message: GroupMessage;
  /** 成员主题色，用于头像与名字。 */
  tone: string;
  onReact: (message: GroupMessage, emoji: string) => void;
  onReply: (message: GroupMessage) => void;
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
}: AssistantGroupMessageProps) {
  const mine = message.author === "user";
  const system = message.author === "system";
  const failed = message.status === "failed";
  /** 本人发送的消息不提供表情回复；表情只给助理的答复。 */
  const canReact = !mine && !system;
  const runningRunId = message.status === "running" ? message.runId : null;
  // 运行状态载荷（如 {"status":"ready"}）渲染成可读状态或按空正文处理，不照搬 JSON。
  // 用户自己发的文字保持原样：这是助理运行记录的清洗，不替用户改写输入。
  const text = message.author === "user" ? message.text : readableGroupMessageText(message.text);
  const body = text ? (
    <AssistantGroupMarkdown text={text} />
  ) : runningRunId ? (
    <RunningReplyStatus runId={runningRunId} />
  ) : (
    <span className={styles.placeholder}>{statusText(message.status)}</span>
  );
  const quote = message.replyTo ? (
    <div className={styles.quoted} aria-label={`引用 ${message.replyTo.authorName}`}>
      <span className={styles.quotedAuthor}>{message.replyTo.authorName}</span>
      <span className={styles.quotedText}>{
        message.replyTo.author === "user" ? message.replyTo.text : readableGroupMessageText(message.replyTo.text)
      }</span>
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
      copyText={text}
      className={styles.actions}
      reactionsClassName={styles.reactionRow}
    />
  );

  if (system) {
    return (
      <article className={styles.systemRow} data-author="system" data-group-message-id={message.messageId}>
        <div className={`${styles.systemPill} ${failed ? styles.failed : ""}`}>{body}</div>
      </article>
    );
  }

  if (mine) {
    return (
      <article className={styles.userRow} data-author="user" data-group-message-id={message.messageId}>
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
    <article className={styles.memberRow} data-author={message.author} data-group-message-id={message.messageId}>
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
