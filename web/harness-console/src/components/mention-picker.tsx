"use client";

import { BotRailAvatar, botAccent } from "./bot-rail";
import styles from "./mention-picker.module.css";

/**
 * 群聊与单助理共用的 @ 选择器。
 * 只负责“光标前是一个 @ 词”的判定、过滤和菜单渲染；
 * 键盘导航和文本替换由调用方按各自输入框的既有约定执行。
 */

export type MentionTarget = {
  memberId: string;
  name: string;
  role?: "creator" | "primary" | "specialist";
  /** 单助理侧取自助理外观；群成员由机器人 ID 稳定生成。 */
  tone?: string;
  shape?: string | null;
};

export const MENTION_ROLE_LABEL: Record<NonNullable<MentionTarget["role"]>, string> = {
  creator: "创建者",
  primary: "主助理",
  specialist: "子助理",
};

/** 光标前最近的一段 @ 词：任意位置都能唤出选择面板，不要求前面是空格。 */
const MENTION_TOKEN = /@([^\s@]*)$/u;

export function mentionTrigger(text: string, caret: number) {
  const match = MENTION_TOKEN.exec(text.slice(0, caret));
  if (!match) return null;
  return { start: caret - match[0].length, end: caret, query: match[1].toLowerCase() };
}

export function filterMentionTargets(members: readonly MentionTarget[], query: string) {
  return members.filter((member) =>
    `${member.name} ${member.role ? MENTION_ROLE_LABEL[member.role] : ""}`
      .toLowerCase()
      .includes(query),
  );
}

type MentionMenuProps = {
  id: string;
  members: readonly MentionTarget[];
  activeIndex: number;
  onChoose: (member: MentionTarget) => void;
  /** 单助理的可提及清单不是群成员，不沿用“成员”措辞。 */
  emptyText?: string;
  label?: string;
  testId?: string;
};

export function MentionMenu({
  id,
  members,
  activeIndex,
  onChoose,
  emptyText = "没有匹配的成员",
  label = "选择要提到的助理",
  testId = "mention-menu",
}: MentionMenuProps) {
  return (
    <div
      className={styles.menu}
      id={id}
      role="listbox"
      aria-label={label}
      data-testid={testId}
    >
      {members.length === 0 ? (
        <p className={styles.empty}>{emptyText}</p>
      ) : (
        members.map((member, index) => (
          <div
            key={member.memberId}
            id={`${id}-${index}`}
            role="option"
            aria-selected={index === activeIndex}
            className={styles.item}
            onMouseDown={(event) => event.preventDefault()}
            onClick={() => onChoose(member)}
          >
            <BotRailAvatar
              tone={member.tone ?? botAccent(member.memberId)}
              shape={member.shape ?? undefined}
            />
            <span className={styles.name}>{member.name}</span>
            <span className={styles.role}>
              {member.role ? MENTION_ROLE_LABEL[member.role] : ""}
            </span>
          </div>
        ))
      )}
    </div>
  );
}
