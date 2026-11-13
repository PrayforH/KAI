"use client";

import {
  useEffect,
  useId,
  useRef,
  useState,
  type ChangeEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type RefObject,
} from "react";

import type { GroupLibraryDocument } from "../lib/assistant-client";
import { BotRailAvatar, botAccent } from "./bot-rail";
import styles from "./assistant-group-composer.module.css";

/** 引用条只展示作者与截断后的文本，真正的引用 id 由父组件持有。 */
export type GroupQuotePreview = {
  authorName: string;
  text: string;
};

type MentionTarget = {
  memberId: string;
  name: string;
  role?: "creator" | "primary" | "specialist";
};

type AssistantGroupComposerProps = {
  value: string;
  onChange: (value: string) => void;
  onSend: () => void;
  sending: boolean;
  disabled?: boolean;
  placeholder: string;
  members: MentionTarget[];
  quoted: GroupQuotePreview | null;
  onClearQuote: () => void;
  textareaRef?: RefObject<HTMLTextAreaElement | null>;
  /** 群资料库文档；「+」里列出可随消息发送的文件。 */
  libraryDocuments?: GroupLibraryDocument[];
  /** 资料库读取状态：读取中或暂时不可用时给准确文案。 */
  libraryStatus?: "loading" | "ready" | "unavailable";
  /** 已选、将随下一条消息发送的文档 id。 */
  attachmentIds?: string[];
  onToggleAttachment?: (documentId: string) => void;
  /** 打开右侧资料库面板。 */
  onOpenLibrary?: () => void;
};

const LINE_HEIGHT = 22;
const PADDING_Y = 20;
const MIN_HEIGHT = LINE_HEIGHT + PADDING_Y;
/** 与 CSS 的 max-height 一致：5 行（22 × 5）加上下内边距 20 = 130，超过后内部滚动，不再撑高。 */
const MAX_HEIGHT = 130;
const MAX_ATTACHMENTS = 10;

/** 光标前最近的一段 @ 词：任意位置都能唤出选择面板，不要求前面是空格。 */
const MENTION_TOKEN = /@([^\s@]*)$/u;

const MEMBER_ROLE_LABEL: Record<NonNullable<MentionTarget["role"]>, string> = {
  creator: "创建者",
  primary: "主助理",
  specialist: "子助理",
};

function mentionTrigger(text: string, caret: number) {
  const match = MENTION_TOKEN.exec(text.slice(0, caret));
  if (!match) return null;
  return { start: caret - match[0].length, end: caret, query: match[1].toLowerCase() };
}

function PlusIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
      <path d="M10 4.5v11M4.5 10h11" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" />
    </svg>
  );
}

/**
 * 群聊输入框：单行 44px 外框，Shift+Enter 换行，长到约 5 行后内部滚动。
 * 提示文字在卡片外，不占输入框高度；@、+、引用条、附件都在同一块里。
 */
export function AssistantGroupComposer({
  value,
  onChange,
  onSend,
  sending,
  disabled = false,
  placeholder,
  members,
  quoted,
  onClearQuote,
  textareaRef,
  libraryDocuments = [],
  libraryStatus = "ready",
  attachmentIds = [],
  onToggleAttachment,
  onOpenLibrary,
}: AssistantGroupComposerProps) {
  const ownRef = useRef<HTMLTextAreaElement | null>(null);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const [menu, setMenu] = useState<"add" | null>(null);
  const [caret, setCaret] = useState(0);
  const [activeMention, setActiveMention] = useState(0);
  const [dismissedMention, setDismissedMention] = useState<number | null>(null);
  const [composing, setComposing] = useState(false);
  const mentionListId = useId();

  const setTextarea = (node: HTMLTextAreaElement | null) => {
    ownRef.current = node;
    if (textareaRef) textareaRef.current = node;
  };

  // 跟随内容长高，到上限封顶后内部滚动；清空后回到一行。
  useEffect(() => {
    const node = ownRef.current;
    if (!node) return;
    node.style.height = "auto";
    node.style.height = `${Math.max(MIN_HEIGHT, Math.min(node.scrollHeight, MAX_HEIGHT))}px`;
    node.style.overflowY = node.scrollHeight > MAX_HEIGHT ? "auto" : "hidden";
  }, [value]);

  useEffect(() => {
    if (!menu) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setMenu(null);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenu(null);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [menu]);

  // 输入法组词时不弹面板，避免拼音里的 @ 触发误选。
  const trigger = composing ? null : mentionTrigger(value, Math.min(caret, value.length));
  const mentionOpen =
    Boolean(trigger) &&
    trigger!.start !== dismissedMention &&
    members.length > 0 &&
    !disabled;
  const filteredMembers = trigger
    ? members.filter((member) =>
        `${member.name} ${member.role ? MEMBER_ROLE_LABEL[member.role] : ""}`
          .toLowerCase()
          .includes(trigger.query),
      )
    : [];

  useEffect(() => {
    setActiveMention(0);
  }, [trigger?.start, trigger?.query]);

  const chooseMention = (member: MentionTarget) => {
    if (!trigger) return;
    const nextCaret = trigger.start + member.name.length + 2;
    onChange(`${value.slice(0, trigger.start)}@${member.name} ${value.slice(trigger.end)}`);
    setCaret(nextCaret);
    setDismissedMention(null);
    requestAnimationFrame(() => {
      const node = ownRef.current;
      node?.focus();
      node?.setSelectionRange(nextCaret, nextCaret);
    });
  };

  const handleChange = (event: ChangeEvent<HTMLTextAreaElement>) => {
    const next = event.target.value;
    const nextCaret = event.target.selectionStart ?? next.length;
    onChange(next);
    setCaret(nextCaret);
    setDismissedMention(null);
  };

  const handleKeyDown = (event: ReactKeyboardEvent<HTMLTextAreaElement>) => {
    // 中文输入法选词也会发 keydown Enter；这时只确认候选词，不能发消息。
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    if (mentionOpen && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
      event.preventDefault();
      const step = event.key === "ArrowDown" ? 1 : -1;
      setActiveMention((index) =>
        filteredMembers.length ? (index + step + filteredMembers.length) % filteredMembers.length : 0,
      );
      return;
    }
    if (mentionOpen && (event.key === "Enter" || event.key === "Tab")) {
      const member = filteredMembers[Math.min(activeMention, filteredMembers.length - 1)];
      if (member) {
        event.preventDefault();
        chooseMention(member);
        return;
      }
    }
    if (mentionOpen && event.key === "Escape") {
      event.preventDefault();
      setDismissedMention(trigger?.start ?? null);
      return;
    }
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      if (!sending && !disabled) onSend();
    }
  };

  const selectedDocuments = attachmentIds.flatMap((id) => {
    const document = libraryDocuments.find((item) => item.documentId === id);
    return document ? [document] : [];
  });
  const atAttachmentLimit = attachmentIds.length >= MAX_ATTACHMENTS;

  return (
    <div className={styles.root} ref={rootRef}>
      <div className={styles.composer} data-testid="group-composer-card">
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
        {selectedDocuments.length > 0 && (
          <div className={styles.attachmentBar} aria-label="将随消息发送的文件">
            {selectedDocuments.map((document) => (
              <span key={document.documentId} className={styles.attachmentChip}>
                <span className={styles.attachmentName} title={document.name}>{document.name}</span>
                <button
                  type="button"
                  className={styles.attachmentRemove}
                  aria-label={`移除附件 ${document.name}`}
                  title="移除附件"
                  onClick={() => onToggleAttachment?.(document.documentId)}
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        )}
        <div className={styles.row}>
          <div className={styles.toolWrap}>
            <button
              type="button"
              className={styles.toolButton}
              aria-label={
                attachmentIds.length
                  ? `添加附件或知识库，已选 ${attachmentIds.length} 个文件`
                  : "添加附件或知识库"
              }
              title="添加附件或知识库"
              aria-haspopup="menu"
              aria-expanded={menu === "add"}
              disabled={disabled}
              onClick={() => setMenu((current) => (current === "add" ? null : "add"))}
            >
              <PlusIcon />
            </button>
            {menu === "add" && (
              <div className={`${styles.menu} ${styles.addMenu}`} role="menu" aria-label="添加附件或知识库">
                <p className={styles.menuTitle}>群资料库</p>
                {libraryStatus !== "ready" ? (
                  <p className={styles.menuEmpty}>
                    {libraryStatus === "loading" ? "正在读取资料库…" : "资料库暂时不可用"}
                  </p>
                ) : libraryDocuments.length === 0 ? (
                  <p className={styles.menuEmpty}>资料库里还没有文件</p>
                ) : (
                  libraryDocuments.map((document) => {
                    const checked = attachmentIds.includes(document.documentId);
                    return (
                      <button
                        key={document.documentId}
                        type="button"
                        role="menuitemcheckbox"
                        aria-checked={checked}
                        className={styles.menuItem}
                        disabled={!checked && atAttachmentLimit}
                        onClick={() => onToggleAttachment?.(document.documentId)}
                      >
                        <span className={styles.menuCheck} aria-hidden="true">{checked ? "✓" : ""}</span>
                        <span className={styles.menuName}>{document.name}</span>
                      </button>
                    );
                  })
                )}
                <button
                  type="button"
                  role="menuitem"
                  className={`${styles.menuItem} ${styles.menuAction}`}
                  onClick={() => {
                    setMenu(null);
                    onOpenLibrary?.();
                  }}
                >
                  打开资料库
                </button>
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
            aria-autocomplete="list"
            aria-controls={mentionOpen ? mentionListId : undefined}
            aria-expanded={mentionOpen}
            aria-activedescendant={
              mentionOpen && filteredMembers[activeMention]
                ? `${mentionListId}-${activeMention}`
                : undefined
            }
            disabled={disabled}
            onChange={handleChange}
            onSelect={(event) => setCaret(event.currentTarget.selectionStart ?? 0)}
            onBlur={() => setDismissedMention(trigger?.start ?? null)}
            onCompositionStart={() => setComposing(true)}
            onCompositionEnd={() => setComposing(false)}
            onKeyDown={handleKeyDown}
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
        {mentionOpen && (
          <div
            className={styles.mentionMenu}
            id={mentionListId}
            role="listbox"
            aria-label="选择要提到的助理"
            data-testid="group-mention-menu"
          >
            {filteredMembers.length === 0 ? (
              <p className={styles.mentionEmpty}>没有匹配的成员</p>
            ) : (
              filteredMembers.map((member, index) => (
                <div
                  key={member.memberId}
                  id={`${mentionListId}-${index}`}
                  role="option"
                  aria-selected={index === activeMention}
                  className={styles.mentionItem}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => chooseMention(member)}
                >
                  <BotRailAvatar tone={botAccent(member.memberId)} />
                  <span className={styles.mentionName}>{member.name}</span>
                  <span className={styles.mentionRole}>
                    {member.role ? MEMBER_ROLE_LABEL[member.role] : ""}
                  </span>
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
}
