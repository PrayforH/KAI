"use client";

import { useEffect, useRef, useState, type RefObject } from "react";

import styles from "./assistant-group-composer.module.css";

/** 引用条只展示作者与截断后的文本，真正的引用 id 由父组件持有。 */
export type GroupQuotePreview = {
  authorName: string;
  text: string;
};

type MentionTarget = {
  memberId: string;
  name: string;
};

type AssistantGroupComposerProps = {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  /** 由父组件统一写入草稿；未提供时输入框自己拼接。 */
  onMention?: (name: string) => void;
  sending: boolean;
  disabled?: boolean;
  placeholder: string;
  members: MentionTarget[];
  quoted: GroupQuotePreview | null;
  onClearQuote: () => void;
  textareaRef?: RefObject<HTMLTextAreaElement | null>;
  hint?: string;
};

const LINE_HEIGHT = 22;
const MAX_LINES = 5;
const PADDING_Y = 20;

/**
 * 群聊输入框：默认一行（40px 左右），Shift+Enter 换行，
 * 最多长到 5 行后内部滚动；@、引用条、发送都在同一块里。
 */
export function AssistantGroupComposer({
  value,
  onChange,
  onSend,
  onMention,
  sending,
  disabled = false,
  placeholder,
  members,
  quoted,
  onClearQuote,
  textareaRef,
  hint,
}: AssistantGroupComposerProps) {
  const ownRef = useRef<HTMLTextAreaElement | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [mentionOpen, setMentionOpen] = useState(false);

  const setTextarea = (node: HTMLTextAreaElement | null) => {
    ownRef.current = node;
    if (textareaRef) textareaRef.current = node;
  };

  // 跟随内容长高，到 5 行封顶；清空后回到一行。
  useEffect(() => {
    const node = ownRef.current;
    if (!node) return;
    node.style.height = "auto";
    const min = LINE_HEIGHT + PADDING_Y;
    const max = LINE_HEIGHT * MAX_LINES + PADDING_Y;
    node.style.height = `${Math.max(min, Math.min(node.scrollHeight, max))}px`;
    node.style.overflowY = node.scrollHeight > max ? "auto" : "hidden";
  }, [value]);

  useEffect(() => {
    if (!mentionOpen) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setMentionOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMentionOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [mentionOpen]);

  const insertMention = (name: string) => {
    if (onMention) {
      onMention(name);
    } else {
      const spacer = value && !value.endsWith(" ") ? " " : "";
      onChange(`${value}${spacer}@${name} `);
    }
    setMentionOpen(false);
    requestAnimationFrame(() => ownRef.current?.focus());
  };

  return (
    <div className={styles.composer} ref={rootRef}>
      {quoted && (
        <div className={styles.quoteBar}>
          <span className={styles.quoteAuthor}>{quoted.authorName}</span>
          <span className={styles.quoteText}>{quoted.text}</span>
          <button
            type="button"
            className={styles.quoteClose}
            aria-label="取消引用"
            title="取消引用"
            onClick={onClearQuote}
          >
            ×
          </button>
        </div>
      )}
      <div className={styles.row}>
        <div className={styles.mentionWrap}>
          <button
            type="button"
            className={styles.toolButton}
            aria-label="提到群成员"
            title="提到群成员"
            aria-haspopup="menu"
            aria-expanded={mentionOpen}
            disabled={disabled || members.length === 0}
            onClick={() => setMentionOpen((open) => !open)}
          >
            @
          </button>
          {mentionOpen && (
            <div className={styles.mentionMenu} role="group" aria-label="选择要提到的成员">
              {members.map((member) => (
                <button
                  key={member.memberId}
                  type="button"
                  className={styles.mentionItem}
                  onClick={() => insertMention(member.name)}
                >
                  {member.name}
                </button>
              ))}
            </div>
          )}
        </div>
        <textarea
          ref={setTextarea}
          className={styles.input}
          value={value}
          rows={1}
          placeholder={placeholder}
          aria-label="消息内容"
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              if (!sending && !disabled) onSend();
            }
          }}
        />
        <button
          type="button"
          className={styles.send}
          aria-label="发送"
          title="发送"
          aria-busy={sending}
          disabled={!value.trim() || sending || disabled}
          onClick={() => onSend()}
        >
          <svg viewBox="0 0 24 24" fill="none" aria-hidden="true">
            <path
              d="M12 19V5M12 5l-6 6M12 5l6 6"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
      </div>
      {hint && <span className={styles.hint}>{hint}</span>}
    </div>
  );
}
