"use client";

import { useEffect, useRef, useState } from "react";

import { writeTextToClipboard } from "../lib/clipboard";
import styles from "./message-actions.module.css";

/** 两层消息操作条共用同一套表情，避免群聊与助理各有一份。 */
export const MESSAGE_REACTIONS = ["👍", "❤️", "😂", "🎉", "👀"];

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

export type MessageActionsProps = {
  reactions?: readonly string[];
  onReact?: (emoji: string) => void;
  onReply?: () => void;
  /** 传了文本就显示复制；自己处理复制时用 onCopy。 */
  copyText?: string;
  onCopy?: () => void;
  canDelete?: boolean;
  onDelete?: () => void;
  /** 删除是危险操作，文案可随场景调整。 */
  deleteTitle?: string;
  deleteConfirmHint?: string;
  /** 群聊沿用既有操作条的浮现时机与尺寸。 */
  className?: string;
  reactionsClassName?: string;
  /** 额外操作，如任务会话里的“编辑消息”。 */
  children?: React.ReactNode;
};

/**
 * 消息操作条：表情、引用、复制、删除。群聊和单助理共用一套实现。
 * 删除点一次进入确认态，再点一次才真正删除；失败由调用方给可读错误。
 */
export function MessageActions({
  reactions = [],
  onReact,
  onReply,
  copyText,
  onCopy,
  canDelete = false,
  onDelete,
  deleteTitle = "删除",
  deleteConfirmHint = "再点一次确认删除",
  className,
  reactionsClassName,
  children,
}: MessageActionsProps) {
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
    if (onCopy) {
      onCopy();
      return;
    }
    if (!copyText) return;
    const ok = await writeTextToClipboard(copyText);
    setCopyState(ok ? "copied" : "failed");
    clearTimeout(copyTimer.current);
    copyTimer.current = setTimeout(() => setCopyState("idle"), 1600);
  };

  const reactionChips = reactions.length > 0 && onReact ? (
    <div className={reactionsClassName ?? styles.reactionRow}>
      {reactions.map((emoji) => (
        <button
          key={emoji}
          type="button"
          className={`${styles.reactionChip} ${styles.reactionChipOn}`}
          title={`取消 ${emoji}`}
          aria-pressed="true"
          onClick={() => onReact(emoji)}
        >
          {emoji}
        </button>
      ))}
    </div>
  ) : null;

  return (
    <>
      {reactionChips}
      <div className={`${styles.actions}${className ? ` ${className}` : ""}`} ref={actionsRef}>
        <div className={styles.actionButtons}>
          {onReact && (
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
          )}
          {onReply && (
            <button
              type="button"
              className={styles.actionButton}
              aria-label="引用这条消息"
              title="引用"
              onClick={onReply}
            >
              <ReplyIcon />
            </button>
          )}
          {(copyText || onCopy) && (
            <button
              type="button"
              className={styles.actionButton}
              aria-label="复制这条消息"
              title={copyState === "copied" ? "已复制" : copyState === "failed" ? "复制失败" : "复制"}
              disabled={!copyText}
              onClick={() => void copy()}
            >
              <CopyIcon />
            </button>
          )}
          {children}
          {canDelete && onDelete && (
            <button
              type="button"
              className={`${styles.actionButton} ${confirmDelete ? styles.actionDanger : ""}`}
              aria-label={confirmDelete ? "确认删除这条消息" : "删除这条消息"}
              title={confirmDelete ? deleteConfirmHint : deleteTitle}
              onBlur={() => setConfirmDelete(false)}
              onClick={() => {
                if (confirmDelete) {
                  setConfirmDelete(false);
                  onDelete();
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
        {pickerOpen && onReact && (
          <span className={styles.reactionPicker} role="group" aria-label="选择表情">
            {MESSAGE_REACTIONS.map((emoji) => (
              <button
                key={emoji}
                type="button"
                className={`${styles.pickerButton} ${
                  reactions.includes(emoji) ? styles.pickerButtonOn : ""
                }`}
                onClick={() => {
                  setPickerOpen(false);
                  onReact(emoji);
                }}
              >
                {emoji}
              </button>
            ))}
          </span>
        )}
      </div>
    </>
  );
}
