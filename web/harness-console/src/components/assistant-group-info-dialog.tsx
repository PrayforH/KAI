"use client";

import { useEffect, useId, useRef } from "react";

import type { AssistantGroup } from "../lib/assistant-client";
import { botAccent } from "./bot-rail";
import styles from "./assistant-group-info-dialog.module.css";

/**
 * 群资料：点标题带的群名打开，和单助理点名字看资料一样。
 * 只读视图——改名/改说明仍在左侧列表的「⋯ → 群资料…」（仅创建者）；
 * 这里回答三件事：谁接话、这个群是干什么的、有哪些成员（可跳到成员管理）。
 */
export function AssistantGroupInfoDialog({ group, onMembers, onClose }: {
  group: AssistantGroup;
  onMembers: () => void;
  onClose: () => void;
}) {
  const titleId = useId();
  const closeButton = useRef<HTMLButtonElement>(null);

  // 打开时焦点进弹窗；Esc 关闭。
  useEffect(() => {
    closeButton.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const active = group.members.filter((member) => member.status === "active");
  // 未 @ 时主助理接话；老群若还留着历史接话人，如实显示那个成员的名字。
  const replier = group.replyAs && group.replyAs !== "primary"
    ? active.find((member) => member.specialistId === group.replyAs)?.name ?? "主助理"
    : "主助理";
  const description = group.description?.trim() ?? "";

  return (
    <div className={styles.backdrop} role="presentation" onPointerDown={onClose}>
      <div
        className={styles.panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onPointerDown={(event) => event.stopPropagation()}
      >
        <button ref={closeButton} type="button" className={styles.close} aria-label="关闭群资料" onClick={onClose}>
          <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
            <path d="m5 5 10 10M15 5 5 15" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
          </svg>
        </button>

        <header className={styles.header}>
          <div className={styles.faces} aria-hidden="true">
            {active.slice(0, 3).map((member) => (
              <span
                key={member.memberId}
                className={styles.face}
                style={{ background: member.kind === "creator" ? "#6b746e" : botAccent(member.specialistId ?? member.name) }}
              >
                {member.name.slice(0, 1)}
              </span>
            ))}
          </div>
          <h2 id={titleId} className={styles.name}>{group.name}</h2>
        </header>

        <dl className={styles.rows}>
          <div className={styles.row}>
            <dt className={styles.label}>
              <svg className={styles.icon} viewBox="0 0 20 20" aria-hidden="true" focusable="false">
                <path d="M8 4 4 8l4 4" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
                <path d="M4 8h7a5 5 0 0 1 5 5v2" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              </svg>
              谁回复
            </dt>
            <dd className={styles.value}>
              {replier}
              <span className={styles.hint}>未 @ 时由它接话</span>
            </dd>
          </div>

          <div className={styles.row}>
            <dt className={styles.label}>
              <svg className={styles.icon} viewBox="0 0 20 20" aria-hidden="true" focusable="false">
                <path d="M5.5 3.5h6l3 3v10h-9z" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" />
                <path d="M7.5 8.5h5M7.5 11.5h5M7.5 14h3" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
              </svg>
              说明
            </dt>
            <dd className={`${styles.value} ${styles.description}`} data-empty={description ? "false" : "true"}>
              {description || "还没有说明"}
            </dd>
          </div>

          <div className={styles.row}>
            <dt className={styles.label}>
              <svg className={styles.icon} viewBox="0 0 20 20" aria-hidden="true" focusable="false">
                <circle cx="7.5" cy="7" r="2.5" fill="none" stroke="currentColor" strokeWidth="1.4" />
                <path d="M3 16c.6-2.6 2.4-4 4.5-4s3.9 1.4 4.5 4" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
                <circle cx="14" cy="7.5" r="2" fill="none" stroke="currentColor" strokeWidth="1.3" />
                <path d="M13.2 12.2c1.9.3 3.2 1.6 3.7 3.8" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
              </svg>
              成员
            </dt>
            <dd className={styles.value}>
              <button type="button" className={styles.members} aria-haspopup="dialog" onClick={onMembers}>
                {active.length} 人
                <svg className={styles.chevron} viewBox="0 0 20 20" aria-hidden="true" focusable="false">
                  <path d="m8 5 5 5-5 5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
                </svg>
              </button>
            </dd>
          </div>
        </dl>
      </div>
    </div>
  );
}
