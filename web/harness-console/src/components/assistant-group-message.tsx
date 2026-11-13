"use client";

import { useEffect, useRef, useState } from "react";

import type { GroupMessage } from "../lib/assistant-client";
import { writeTextToClipboard } from "../lib/clipboard";
import { groupTimeLabel } from "../lib/group-chat-time";
import { AssistantGroupMarkdown } from "./assistant-group-markdown";
import styles from "./assistant-group-message.module.css";

export const GROUP_REACTIONS = ["👍", "❤️", "😂", "🎉", "👀"];

const ROLE_LABEL: Record<GroupMessage["author"], string> = {
  user: "我",
  primary: "主助理",
  specialist: "子助理",
  system: "",
};

function ReplyIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
      <path
        d="M8.5 5.5 3.5 10l5 4.5V12c4 0 6.5 1 8 4-.5-4-2.5-7-8-7V5.5Z"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinejoin="round"
      />
    </svg>
  );
}

function CopyIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
      <rect x="7" y="7" width="9" height="9" rx="2" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <path d="M13 7V5.5A1.5 1.5 0 0 0 11.5 4H5.5A1.5 1.5 0 0 0 4 5.5v6A1.5 1.5 0 0 0 5.5 13H7" fill="none" stroke="currentColor" strokeWidth="1.6" />
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
      <path d="M4.5 6h11M8 6V4.5h4V6m-5.5 0 .6 9h5.8l.6-9" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function AttachmentIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
      <path d="M12.5 6.5 7 12a2.5 2.5 0 0 0 3.5 3.5l5-5a4 4 0 0 0-5.7-5.7l-5 5a5.5 5.5 0 0 0 7.8 7.8l4-4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function SmileIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
      <circle cx="10" cy="10" r="6.5" fill="none" stroke="currentColor" strokeWidth="1.6" />
      <path d="M7.2 11.4a3.2 3.2 0 0 0 5.6 0" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      <circle cx="7.7" cy="8.3" r="0.9" fill="currentColor" />
      <circle cx="12.3" cy="8.3" r="0.9" fill="currentColor" />
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
  const [pickerOpen, setPickerOpen] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const actionsRef = useRef<HTMLDivElement | null>(null);
  const copyTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  useEffect(() => () => clearTimeout(copyTimer.current), []);

  useEffect(() => {
    if (!pickerOpen) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!actionsRef.current?.contains(event.target as Node)) setPickerOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPickerOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [pickerOpen]);

  const copy = async () => {
    const ok = await writeTextToClipboard(message.text);
    setCopyState(ok ? "copied" : "failed");
    clearTimeout(copyTimer.current);
    copyTimer.current = setTimeout(() => setCopyState("idle"), 1600);
  };

  const mine = message.author === "user";
  const system = message.author === "system";
  const failed = message.status === "failed";
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
      {message.attachments.map((attachment) => (
        <li key={attachment.documentId}>
          <a
            className={styles.attachment}
            href={attachment.downloadUrl ?? "#"}
            target="_blank"
            rel="noreferrer"
            title={`下载 ${attachment.name}`}
          >
            <AttachmentIcon />
            <span>{attachment.name}</span>
          </a>
        </li>
      ))}
    </ul>
  ) : null;
  const reactions = message.reactions.length > 0 && (
    <div className={styles.reactionRow}>
      {message.reactions.map((emoji) => (
        <button
          key={emoji}
          type="button"
          className={`${styles.reactionChip} ${styles.reactionChipOn}`}
          title={`取消 ${emoji}`}
          aria-pressed="true"
          onClick={() => onReact(message, emoji)}
        >
          {emoji}
        </button>
      ))}
    </div>
  );
  const actions = (
    <div className={styles.actions} ref={actionsRef}>
      <div className={styles.actionButtons}>
        <button
          type="button"
          className={styles.actionButton}
          aria-label="给这条消息加表情"
          title="加表情"
          aria-haspopup="menu"
          aria-expanded={pickerOpen}
          onClick={() => setPickerOpen((open) => !open)}
        >
          <SmileIcon />
        </button>
        <button
          type="button"
          className={styles.actionButton}
          aria-label="引用这条消息"
          title="引用"
          onClick={() => onReply(message)}
        >
          <ReplyIcon />
        </button>
        <button
          type="button"
          className={styles.actionButton}
          aria-label="复制这条消息"
          title={copyState === "copied" ? "已复制" : copyState === "failed" ? "复制失败" : "复制"}
          disabled={!message.text}
          onClick={() => void copy()}
        >
          <CopyIcon />
        </button>
        {canDelete && onDelete && (
          <button
            type="button"
            className={`${styles.actionButton} ${confirmDelete ? styles.actionDanger : ""}`}
            aria-label={confirmDelete ? "确认删除这条消息" : "删除这条消息"}
            title={confirmDelete ? "再点一次确认删除" : "删除"}
            onBlur={() => setConfirmDelete(false)}
            onClick={() => {
              if (confirmDelete) {
                setConfirmDelete(false);
                onDelete(message);
                return;
              }
              setConfirmDelete(true);
            }}
          >
            {confirmDelete ? <span className={styles.confirmText}>确认？</span> : <TrashIcon />}
          </button>
        )}
      </div>
      {copyState !== "idle" && (
        <span className={styles.copyFeedback} role="status">
          {copyState === "copied" ? "已复制" : "复制失败"}
        </span>
      )}
      {pickerOpen && (
        <span className={styles.reactionPicker} role="group" aria-label="选择表情">
          {GROUP_REACTIONS.map((emoji) => (
            <button
              key={emoji}
              type="button"
              className={`${styles.pickerButton} ${
                message.reactions.includes(emoji) ? styles.pickerButtonOn : ""
              }`}
              onClick={() => {
                setPickerOpen(false);
                onReact(message, emoji);
              }}
            >
              {emoji}
            </button>
          ))}
        </span>
      )}
    </div>
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
          <div className={`${styles.userBubble} ${failed ? styles.failedBubble : ""}`}>{body}</div>
          {attachments}
          {reactions}
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
        <div className={`${styles.memberBody} ${failed ? styles.failed : ""}`}>{body}</div>
        {attachments}
        {reactions}
        {actions}
      </div>
    </article>
  );
}
