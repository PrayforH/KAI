"use client";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { Specialist } from "../lib/assistant-client";
import { botSidebar, type BotSidebar } from "../lib/bot-sidebar";
import styles from "./bot-rail-menu.module.css";

export function BotRailMenu({ bot, onUpdate, onEdit, onArchive }: {
  bot: Specialist;
  onUpdate: (patch: Partial<BotSidebar>) => Promise<void>;
  onEdit: () => void; onArchive: () => Promise<void>;
}) {
  const anchor = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<{left: number; top: number} | null>(null);
  const [mode, setMode] = useState<"main" | "archive">("main");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const sidebar = botSidebar(bot);
  function close() { if (busy) return; setPosition(null); anchor.current?.focus(); }
  useEffect(() => {
    if (!position) return;
    if (!busy) panel.current?.querySelector<HTMLButtonElement>("button:not(:disabled)")?.focus();
    function outside(event: PointerEvent) {
      if (!panel.current?.contains(event.target as Node) && !anchor.current?.contains(event.target as Node)) setPosition(null);
    }
    function escape(event: KeyboardEvent) {
      if (event.key === "Escape" && !busy) { setPosition(null); anchor.current?.focus(); }
    }
    document.addEventListener("pointerdown", outside); document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", outside); document.removeEventListener("keydown", escape); };
  }, [position, busy, mode]);
  async function apply(action: () => Promise<void>) {
    setBusy(true); setError("");
    try { await action(); setPosition(null); anchor.current?.focus(); }
    catch (cause) { setError(cause instanceof Error ? cause.message : "操作未完成，请重试。"); }
    finally { setBusy(false); }
  }
  const item = (label: string, action: () => void, danger = false) => <button role="menuitem" type="button" disabled={busy}
    className={danger ? styles.danger : undefined} onClick={action}>{label}</button>;
  return <>
    <button ref={anchor} type="button" className="bot-rail-menu-trigger" aria-label={`${bot.name} 的更多操作`}
      aria-haspopup="menu" aria-expanded={Boolean(position)} onClick={() => {
        if (position) { close(); return; }
        const rect = anchor.current!.getBoundingClientRect();
        setMode("main"); setError(""); setPosition({left: Math.max(8, Math.min(rect.left, window.innerWidth - 260)), top: Math.max(8, Math.min(rect.bottom + 4, window.innerHeight - 360))});
      }}>···</button>
    {position && createPortal(<div ref={panel} className={styles.menu} style={position}
      role={mode === "archive" ? "alertdialog" : "menu"} aria-label={mode === "archive" ? `归档 ${bot.name}` : `${bot.name} 的更多操作`}
      onKeyDown={event => {
        if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key) || event.target instanceof HTMLInputElement) return;
        const items = [...panel.current!.querySelectorAll<HTMLButtonElement>('button:not(:disabled)')];
        const index = items.indexOf(document.activeElement as HTMLButtonElement);
        const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
        event.preventDefault(); items[next]?.focus();
      }}>
      {mode === "main" && <>
        {item(sidebar.pinned ? "取消置顶" : "置顶", () => void apply(() => onUpdate({pinned: !sidebar.pinned})))}
        {item("编辑资料", () => { setPosition(null); onEdit(); })}
        <hr />{item("归档 Bot…", () => setMode("archive"), true)}
      </>}
      {mode === "archive" && <>
        <strong>归档“{bot.name}”？</strong>
        <p>不再接受新的职责执行，聊天与成果保留，可在设置的「机器人」中恢复。已开始的任务不会被强制中断。</p>
        <div className={styles.actions}><button type="button" disabled={busy} onClick={close}>取消</button>
          <button type="button" disabled={busy} className={styles.danger} onClick={() => void apply(onArchive)}>归档</button></div>
      </>}
      {busy && <p role="status">正在保存…</p>}{error && <p className={styles.error} role="alert">{error}</p>}
    </div>, document.body)}
  </>;
}
