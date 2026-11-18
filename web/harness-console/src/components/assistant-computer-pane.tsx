"use client";

import { useRef, useState, type ReactNode } from "react";
import { ComputerDesktop } from "./computer-desktop";
import styles from "./computer-desktop.module.css";

/** 三栏时历史面板固定占 32%，与参考稿「聊天 38 / 历史 32 / 电脑 30」一致。 */
const HISTORY_SHARE = 32;
/** 三栏时电脑列的可用区间，保证聊天列始终还能放下一条消息气泡。 */
const THREE_PANE_MIN = 25;
const THREE_PANE_MAX = 45;
/** 两栏拖拽的区间（35–70）。 */
const TWO_PANE_MIN = 35;
const TWO_PANE_MAX = 70;

const clamp = (value: number, min: number, max: number) =>
  Math.max(min, Math.min(max, value));

/**
 * 聊天区网格：聊天 |（历史）|（分隔条 + 电脑）。
 *
 * 历史面板是网格里的一列，不是浮层，所以不会再压住桌面画面。三栏时历史列
 * 固定 32%，拖拽只调电脑列、聊天列吃剩余——分隔条始终是真的能拖的。
 */
export function AssistantComputerPane({ open, onClose, children, botId, historyOpen = false, history }: {
  open: boolean; onClose: () => void; children: ReactNode; botId?: string | null;
  historyOpen?: boolean; history?: ReactNode;
}) {
  const container = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(56);

  // 三栏：电脑列取两栏宽度的 (100-32)/100 折算，默认 56 → 38，正好对上参考稿。
  const computerShare = historyOpen
    ? clamp(((100 - HISTORY_SHARE) * width) / 100, THREE_PANE_MIN, THREE_PANE_MAX)
    : clamp(width, TWO_PANE_MIN, TWO_PANE_MAX);
  const chatShare = 100 - HISTORY_SHARE - computerShare;

  let gridTemplateColumns: string | undefined;
  if (open && historyOpen) {
    gridTemplateColumns = `minmax(240px, ${chatShare}fr) minmax(0, ${HISTORY_SHARE}fr) 6px minmax(0, ${computerShare}fr)`;
  } else if (open) {
    gridTemplateColumns = `minmax(280px, ${100 - computerShare}fr) 6px minmax(0, ${computerShare}fr)`;
  } else if (historyOpen) {
    gridTemplateColumns = `minmax(280px, 46fr) minmax(0, 54fr)`;
  }

  const nudge = (delta: number) => {
    setWidth(value => clamp(value + delta, TWO_PANE_MIN, TWO_PANE_MAX));
  };

  return <div className={styles.workspace} ref={container}
    data-split={open ? "true" : "false"} data-history={historyOpen ? "true" : "false"}
    style={gridTemplateColumns ? { gridTemplateColumns } : undefined}>
    {children}
    {historyOpen && <div className={styles.historySlot} data-stacked={open ? "true" : "false"}>{history}</div>}
    {open && <>
      <div className={styles.divider} role="separator" aria-label="调整聊天与电脑宽度" aria-orientation="vertical"
        aria-valuemin={historyOpen ? THREE_PANE_MIN : TWO_PANE_MIN}
        aria-valuemax={historyOpen ? THREE_PANE_MAX : TWO_PANE_MAX}
        aria-valuenow={Math.round(computerShare)} tabIndex={0}
        onKeyDown={event => {
          if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
            event.preventDefault(); nudge(event.key === "ArrowLeft" ? 2 : -2);
          }
        }}
        onPointerDown={event => { event.currentTarget.setPointerCapture(event.pointerId); }}
        onPointerMove={event => {
          if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
          const bounds = container.current?.getBoundingClientRect();
          if (bounds) setWidth(clamp((bounds.right - event.clientX) / bounds.width * 100, TWO_PANE_MIN, TWO_PANE_MAX));
        }}
        onPointerUp={event => { if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }} />
      <aside id="assistant-computer-pane" className={styles.pane}><ComputerDesktop key={botId ?? "primary"} embedded onClose={onClose} botId={botId} /></aside>
    </>}
  </div>;
}
