"use client";

import { useEffect, useId, useRef, useState } from "react";

import type { AssistantGroup, Specialist } from "../lib/assistant-client";
import { botAccent } from "./bot-rail";
import styles from "./assistant-group-members-dialog.module.css";

/** 与群信息面板同一口径：个人主线程的助理不进群。 */
const PERSONAL_THREAD = "kai-personal-assistant-v1";

export type MemberChange = {
  add: string[];
  remove: { memberId: string; name: string }[];
};

type Props = {
  group: AssistantGroup;
  /** 全部可用子助理（侧栏已加载的那份）。 */
  specialists: Specialist[];
  onApply: (change: MemberChange) => Promise<string | null>;
  onClose: () => void;
};

type Row = {
  key: string;
  name: string;
  kind: "creator" | "primary" | "specialist";
  locked: boolean;
  specialistId: string | null;
  memberId: string | null;
  avatar: string;
};

/**
 * 群成员管理弹窗：一行一个成员，点整行切换勾选；创建者与主助理固定在场。
 * 勾选只改本地状态，「保存」时才按差集发请求，失败留在弹窗里给出原因。
 */
export function AssistantGroupMembersDialog({ group, specialists, onApply, onClose }: Props) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const seated = new Set(
    group.members
      .map((member) => member.specialistId)
      .filter((value): value is string => Boolean(value)),
  );

  const rows: Row[] = [];
  for (const member of group.members) {
    if (member.status !== "active") continue;
    rows.push({
      key: member.memberId,
      name: member.name,
      kind: member.kind,
      locked: member.kind !== "specialist",
      specialistId: member.specialistId,
      memberId: member.memberId,
      avatar:
        member.kind === "creator"
          ? "#6b746e"
          : botAccent(member.specialistId ?? member.name),
    });
  }
  for (const item of specialists) {
    if (item.status === "archived" || seated.has(item.specialistId)) continue;
    if (item.config?.conversation_thread_id === PERSONAL_THREAD) continue;
    rows.push({
      key: `add-${item.specialistId}`,
      name: item.name,
      kind: "specialist",
      locked: false,
      specialistId: item.specialistId,
      memberId: null,
      avatar: botAccent(item.specialistId),
    });
  }

  // 只记用户改过的行，默认值永远是「当前是否在群里」——群数据被刷新后
  // 不会把一个刚入群的子助理误判成待移出。
  const [toggled, setToggled] = useState<Map<string, boolean>>(new Map());
  const isChecked = (row: Row) =>
    row.locked || (row.specialistId ? toggled.get(row.specialistId) ?? Boolean(row.memberId) : false);
  const checkedCount = rows.filter(isChecked).length;

  // 打开时焦点进弹窗；Esc 关闭。
  useEffect(() => {
    const first = panel.current?.querySelector<HTMLElement>("button, input, [tabindex]");
    first?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  const toggle = (row: Row) => {
    if (row.locked || !row.specialistId || saving) return;
    const specialistId = row.specialistId;
    const next = !isChecked(row);
    setToggled((current) => new Map(current).set(specialistId, next));
  };

  const remove = rows
    .filter((row) => row.kind === "specialist" && row.memberId && !isChecked(row))
    .map((row) => ({ memberId: row.memberId as string, name: row.name }));
  const add = rows
    .filter((row) => row.kind === "specialist" && !row.memberId && row.specialistId && isChecked(row))
    .map((row) => row.specialistId as string);
  const dirty = add.length > 0 || remove.length > 0;

  const save = async () => {
    if (saving || !dirty) return;
    setSaving(true);
    setError(null);
    const failure = await onApply({ add, remove });
    if (failure) {
      setError(failure);
      setSaving(false);
      return;
    }
    setSaving(false);
    onClose();
  };

  return (
    <div className={styles.backdrop} role="presentation" onPointerDown={onClose}>
      <div
        className={styles.panel}
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onPointerDown={(event) => event.stopPropagation()}
      >
        <h2 id={titleId} className={styles.title}>成员管理</h2>
        <p className={styles.groupName}>{group.name}</p>

        <ul className={styles.list}>
          {rows.map((row) => {
            const checked = isChecked(row);
            return (
              <li key={row.key}>
                <label className={`${styles.row} ${row.locked ? styles.rowLocked : ""}`}>
                  <input
                    type="checkbox"
                    className={styles.checkbox}
                    checked={checked}
                    disabled={row.locked || saving}
                    onChange={() => toggle(row)}
                  />
                  <span className={styles.avatar} style={{ background: row.avatar }} aria-hidden="true">
                    {row.name.slice(0, 1)}
                  </span>
                  <span className={styles.name}>{row.name}</span>
                  {row.locked && <span className={styles.lockedTag}>固定</span>}
                  <span className={styles.mark} aria-hidden="true" data-checked={checked ? "true" : "false"}>
                    {checked && (
                      <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
                        <path d="m5 10.5 3.2 3.2L15 7" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    )}
                  </span>
                </label>
              </li>
            );
          })}
        </ul>

        {error && <p className={styles.error} role="alert">{error}</p>}

        <footer className={styles.footer}>
          <button type="button" className={styles.cancel} onClick={onClose} disabled={saving}>
            取消
          </button>
          <button
            type="button"
            className={styles.primary}
            disabled={saving || !dirty}
            aria-busy={saving}
            onClick={() => void save()}
          >
            {saving ? "保存中…" : `保存 · ${checkedCount} 个成员`}
          </button>
        </footer>
      </div>
    </div>
  );
}
