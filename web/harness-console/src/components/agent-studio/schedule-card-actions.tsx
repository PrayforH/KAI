"use client";
import { useRef, useState } from "react";
import { ActionMenuPopover } from "../action-menu-popover";
import styles from "./automation-manager.module.css";

/** Card-level actions for one schedule: enable/disable, run now, and a “more”
 *  menu that holds edit and the two-step delete. Keeping this out of the
 *  manager keeps the list markup to its summary and one action strip. */
export function ScheduleCardActions({
  title,
  toggleLabel,
  pending,
  confirmingDelete,
  onToggle,
  onRun,
  onEdit,
  onRequestDelete,
  onDelete,
}: {
  title: string;
  toggleLabel: "启用" | "禁用";
  pending: boolean;
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
      <button type="button" className={styles.cardAction} disabled={pending} onClick={onToggle}>
        {toggleLabel}
      </button>
      <button type="button" className={styles.cardAction} disabled={pending} onClick={onRun}>
        立即执行
      </button>
      <button
        ref={trigger}
        type="button"
        className={styles.cardAction}
        aria-haspopup="menu"
        aria-expanded={menuOpen}
        onClick={() => setMenuOpen((open) => !open)}
      >
        更多 ▾
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
