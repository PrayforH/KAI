"use client";

import { useRef, useState, type ReactNode } from "react";
import { ComputerDesktop } from "./computer-desktop";
import styles from "./computer-desktop.module.css";

export function AssistantComputerPane({ open, onClose, children, botId }: {
  open: boolean; onClose: () => void; children: ReactNode; botId?: string | null;
}) {
  const container = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(56);
  return <div className={`${styles.workspace} ${open ? styles.split : ""}`} ref={container}
    style={open ? { gridTemplateColumns: `minmax(280px, ${100 - width}fr) 6px minmax(0, ${width}fr)` } : undefined}>
    {children}
    {open && <>
      <div className={styles.divider} role="separator" aria-label="调整聊天与电脑宽度" aria-orientation="vertical"
        aria-valuemin={35} aria-valuemax={70} aria-valuenow={width} tabIndex={0}
        onKeyDown={event => {
          if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
            event.preventDefault(); setWidth(value => Math.max(35, Math.min(70, value + (event.key === "ArrowLeft" ? 2 : -2))));
          }
        }}
        onPointerDown={event => { event.currentTarget.setPointerCapture(event.pointerId); }}
        onPointerMove={event => {
          if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
          const bounds = container.current?.getBoundingClientRect();
          if (bounds) setWidth(Math.max(35, Math.min(70, (bounds.right - event.clientX) / bounds.width * 100)));
        }}
        onPointerUp={event => { if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }} />
      <aside id="assistant-computer-pane" className={styles.pane}><ComputerDesktop key={botId ?? "primary"} embedded onClose={onClose} botId={botId} /></aside>
    </>}
  </div>;
}
