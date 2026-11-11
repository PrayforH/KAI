"use client";
import { useRef, useState } from "react";
import { ActionMenuPopover } from "../action-menu-popover";
import styles from "./automation-manager.module.css";

/** Which card action is in flight; the card itself allows only one at a time. */
export type ScheduleCardActionKind = "toggle" | "run" | "delete";

const PlayIcon = () => (
  <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true" focusable="false">
    <path d="M7 4.8v10.4a.8.8 0 0 0 1.22.68l8.3-5.2a.8.8 0 0 0 0-1.36l-8.3-5.2A.8.8 0 0 0 7 4.8Z" fill="currentColor" />
  </svg>
);

const MoreIcon = () => (
  <svg viewBox="0 0 20 20" width="18" height="18" aria-hidden="true" focusable="false">
    <circle cx="4.5" cy="10" r="1.5" fill="currentColor" />
    <circle cx="10" cy="10" r="1.5" fill="currentColor" />
    <circle cx="15.5" cy="10" r="1.5" fill="currentColor" />
  </svg>
);

/** Card-level actions for one schedule: the state switch, run now, and a “more”
 *  menu that holds edit and the two-step delete. The strip is icon-only so a
 *  scheduled task reads like every other binary state in the console, and the
 *  manager keeps only its summary plus this one strip. */
export function ScheduleCardActions({
  title,
  enabled,
  pending,
  pendingAction,
  confirmingDelete,
  onToggle,
  onRun,
  onEdit,
  onRequestDelete,
  onDelete,
}: {
  title: string;
  /** Whether the schedule is currently active; the switch reports this state. */
  enabled: boolean;
  pending: boolean;
  pendingAction?: ScheduleCardActionKind;
  confirmingDelete: boolean;
  onToggle: () => void;
  onRun: () => void;
  onEdit: () => void;
  onRequestDelete: () => void;
  onDelete: () => void;
}) {
  const trigger = useRef<HTMLButtonElement>(null);
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <div className={styles.taskActions}>
      <button
        type="button"
        role="switch"
        aria-checked={enabled}
        aria-label={`${title} 启用状态`}
        className={styles.cardSwitch}
        disabled={pending}
        onClick={onToggle}
      />
      <button
        type="button"
        className={styles.cardIconAction}
        aria-label={`立即执行 ${title}`}
        title="立即执行"
        aria-busy={pendingAction === "run" || undefined}
        disabled={pending}
        onClick={onRun}
      >
        {pendingAction === "run"
          ? <span className={styles.cardSpinner} aria-hidden="true" />
          : <PlayIcon />}
      </button>
      <button
        ref={trigger}
        type="button"
        className={styles.cardIconAction}
        aria-label={`${title} 的更多操作`}
        title="更多操作"
        aria-haspopup="menu"
        aria-expanded={menuOpen}
        disabled={pending && pendingAction === "delete"}
        onClick={() => setMenuOpen((open) => !open)}
      >
        <MoreIcon />
      </button>
      {menuOpen && (
        <ActionMenuPopover
          anchor={trigger}
          label={`${title} 的更多操作`}
          onClose={() => setMenuOpen(false)}
          className={styles.cardMenu}
        >
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setMenuOpen(false);
              onEdit();
            }}
          >
            编辑
          </button>
          <button
            type="button"
            role="menuitem"
            className={styles.menuDanger}
            onClick={() => {
              // First press turns the item into a confirmation; the second one
              // deletes. The menu stays open so the label change is visible.
              if (confirmingDelete) {
                setMenuOpen(false);
                onDelete();
              } else {
                onRequestDelete();
              }
            }}
          >
            {confirmingDelete ? "确认删除？" : "删除"}
          </button>
        </ActionMenuPopover>
      )}
    </div>
  );
}
