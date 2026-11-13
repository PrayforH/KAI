"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { assistantRequest, type AssistantGroup } from "../lib/assistant-client";
import styles from "./bot-rail-menu.module.css";

/**
 * 群聊列表项的操作入口：仅创建者可见，删除/解散需输入群名二次确认。
 * 复用与群信息面板同一套接口（DELETE /v1/studio/assistant/groups/{id}），不新增后端。
 */
export function GroupRailMenu({
  group,
  onDeleted,
}: {
  group: AssistantGroup;
  onDeleted: () => void;
}) {
  const anchor = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null);
  const [mode, setMode] = useState<"main" | "delete">("main");
  const [confirmName, setConfirmName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  function close() {
    if (busy) return;
    setPosition(null);
    setMode("main");
    setConfirmName("");
    anchor.current?.focus();
  }

  useEffect(() => {
    if (!position) return;
    if (!busy) panel.current?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus();
    function outside(event: PointerEvent) {
      if (!panel.current?.contains(event.target as Node) && !anchor.current?.contains(event.target as Node)) {
        if (!busy) close();
      }
    }
    function escape(event: KeyboardEvent) {
      if (event.key === "Escape" && !busy) close();
    }
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [position, busy, mode]);

  async function remove() {
    if (confirmName.trim() !== group.name) return;
    setBusy(true);
    setError("");
    try {
      // 与群信息面板同一个接口，不新增后端端点。
      await assistantRequest<void>(`groups/${encodeURIComponent(group.groupId)}`, "DELETE");
      setPosition(null);
      onDeleted();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "解散群聊失败，请重试。");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <button
        ref={anchor}
        type="button"
        className="bot-rail-menu-trigger"
        aria-label={`${group.name} 的更多操作`}
        aria-haspopup="menu"
        aria-expanded={Boolean(position)}
        onClick={() => {
          if (position) {
            close();
            return;
          }
          const rect = anchor.current!.getBoundingClientRect();
          setMode("main");
          setError("");
          setConfirmName("");
          setPosition({
            left: Math.max(8, Math.min(rect.left, window.innerWidth - 280)),
            top: Math.max(8, Math.min(rect.bottom + 4, window.innerHeight - 320)),
          });
        }}
      >
        ···
      </button>
      {position &&
        createPortal(
          <div
            ref={panel}
            className={styles.menu}
            style={position}
            role={mode === "delete" ? "alertdialog" : "menu"}
            aria-label={mode === "delete" ? `解散 ${group.name}` : `${group.name} 的更多操作`}
          >
            {mode === "main" && (
              <button
                role="menuitem"
                type="button"
                className={styles.danger}
                onClick={() => setMode("delete")}
              >
                删除群聊…
              </button>
            )}
            {mode === "delete" && (
              <>
                <strong>解散“{group.name}”？</strong>
                <p>解散后成员、消息、表情与资料库都会被清空，无法恢复。</p>
                <p>输入群名「{group.name}」确认解散：</p>
                <input
                  value={confirmName}
                  aria-label="输入群名确认解散"
                  placeholder={group.name}
                  disabled={busy}
                  onChange={(event) => setConfirmName(event.target.value)}
                />
                <div className={styles.actions}>
                  <button type="button" disabled={busy} onClick={close}>取消</button>
                  <button
                    type="button"
                    disabled={busy || confirmName.trim() !== group.name}
                    className={styles.danger}
                    onClick={() => void remove()}
                  >
                    {busy ? "解散中…" : "确认解散"}
                  </button>
                </div>
              </>
            )}
            {busy && mode === "main" && <p role="status">正在删除…</p>}
            {error && <p className={styles.error} role="alert">{error}</p>}
          </div>,
          document.body,
        )}
    </>
  );
}
