"use client";

import { useEffect, useRef, useState } from "react";

import { writeTextToClipboard } from "../lib/clipboard";
import {
  findMessageAnchor,
  findScrollParent,
  useMessageAnchorVisibility,
} from "../lib/message-anchor-visibility";
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
  /** 群聊沿用既有操作条的浮现时机与尺寸。 */
  className?: string;
  reactionsClassName?: string;
  /** 操作条不必等 hover 才出（群聊要的「常显」）。 */
  alwaysVisible?: boolean;
  /** 是否在本组件内渲染已选表情；群聊与单助理把表情放到气泡下方，传 false。 */
  renderReactions?: boolean;
  /** 额外操作，如任务会话里的“编辑消息”。 */
  children?: React.ReactNode;
};

type MessageReactionsProps = {
  reactions: readonly string[];
  onReact?: (emoji: string) => void;
  className?: string;
};

/**
 * 已选表情 chip：常显、可点击取消。
 * 独立于操作条，方便挂在气泡下方另起一行（群聊与单助理同一套）。
 */
export function MessageReactions({ reactions, onReact, className }: MessageReactionsProps) {
  if (reactions.length === 0 || !onReact) return null;
  return (
    <div className={className ?? styles.reactionRow}>
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
  );
}

/**
 * 消息操作条：表情、引用、复制。群聊和单助理共用一套实现。
 * 单条消息删除已统一取消，解散群、删会话、删文件不属于这里。
 */
export function MessageActions({
  reactions = [],
  onReact,
  onReply,
  copyText,
  onCopy,
  className,
  reactionsClassName,
  alwaysVisible = false,
  renderReactions = true,
  children,
}: MessageActionsProps) {
  const [pickerOpen, setPickerOpen] = useState(false);
  /** 消息贴着滚动区顶部时，弹层改向下展开，避免被裁掉一半。 */
  const [pickerBelow, setPickerBelow] = useState(false);
  const [copyState, setCopyState] = useState<"idle" | "copied" | "failed">("idle");
  const actionsRef = useRef<HTMLDivElement | null>(null);
  const pickerRef = useRef<HTMLSpanElement | null>(null);
  const copyTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  /** 本条消息的正文/气泡：操作条只随它出现，也只用它判断是否还露在滚动区里。 */
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  /** 指针或键盘焦点落在气泡/操作条上。 */
  const [engaged, setEngaged] = useState(false);

  useEffect(() => () => clearTimeout(copyTimer.current), []);

  // 操作条挂在正文之后，挂载后向上找本条消息的锚点；找不到就按旧行为常驻。
  useEffect(() => {
    setAnchor(findMessageAnchor(actionsRef.current));
  }, []);

  // 整行:hover 会把“只剩操作条可见”也算作悬停；改由气泡本身触发。
  useEffect(() => {
    const actions = actionsRef.current;
    if (!anchor || !actions) return;
    const isInside = (node: Node | null) =>
      node !== null && (anchor.contains(node) || actions.contains(node));
    const enter = () => setEngaged(true);
    const leave = (event: Event) => {
      const related = (event as PointerEvent).relatedTarget as Node | null;
      if (!isInside(related)) setEngaged(false);
    };
    const focusOut = (event: Event) => {
      const related = (event as FocusEvent).relatedTarget as Node | null;
      if (!isInside(related)) setEngaged(false);
    };
    const targets = [anchor, actions];
    for (const target of targets) {
      target.addEventListener("pointerenter", enter);
      target.addEventListener("pointerleave", leave);
      target.addEventListener("focusin", enter);
      target.addEventListener("focusout", focusOut);
    }
    return () => {
      for (const target of targets) {
        target.removeEventListener("pointerenter", enter);
        target.removeEventListener("pointerleave", leave);
        target.removeEventListener("focusin", enter);
        target.removeEventListener("focusout", focusOut);
      }
    };
  }, [anchor]);

  const anchorVisible = useMessageAnchorVisibility(anchor);
  // 气泡几乎完全滚出上沿时，操作条不得悬在空白处：整条隐去且不接收点击。
  const suppressed = anchor !== null && !anchorVisible;

  useEffect(() => {
    if (suppressed) setPickerOpen(false);
  }, [suppressed]);

  // 弹层默认向上展开；若操作条已在滚动区顶部，向上会被裁掉，就翻到下方。
  useEffect(() => {
    if (!pickerOpen) return;
    const picker = pickerRef.current;
    if (!picker) return;
    const scroller = findScrollParent(picker);
    if (!scroller) return;
    const pickerTop = picker.getBoundingClientRect().top;
    setPickerBelow(pickerTop < scroller.getBoundingClientRect().top);
  }, [pickerOpen]);

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

  const reactionChips = renderReactions ? (
    <MessageReactions reactions={reactions} onReact={onReact} className={reactionsClassName} />
  ) : null;

  // 默认：操作条在上、回复在下一行（同一纵向堆叠）。
  // 群聊与单助理把回复挂到气泡下方，传 renderReactions={false} 只留操作条。
  return (
    <div className={styles.stack}>
      <div
        className={`${styles.actions}${className ? ` ${className}` : ""}`}
        ref={actionsRef}
        data-visible={alwaysVisible || engaged ? "true" : "false"}
        data-suppressed={suppressed ? "true" : "false"}
      >
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
        </div>
        {copyState !== "idle" && (
          <span className={styles.copyFeedback} role="status">
            {copyState === "copied" ? "已复制" : "复制失败"}
          </span>
        )}
        {pickerOpen && onReact && (
          <span
            className={`${styles.reactionPicker} ${pickerBelow ? styles.reactionPickerBelow : ""}`}
            ref={pickerRef}
            role="group"
            aria-label="选择表情"
          >
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
      {reactionChips}
    </div>
  );
}
