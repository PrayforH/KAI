"use client";

import { useRef, useState, type ReactNode } from "react";
import { ComputerDesktop } from "./computer-desktop";
import styles from "./computer-desktop.module.css";

/**
 * 历史列宽度：等于右侧助理抽屉（bot-side-panel）的初始宽度。
 * 1440 下容器 1176px，36% = 423px，取 420 正好等于抽屉实测宽度；
 * 窄屏按 36% 收（1280 → 366px，1180 → 330px），聊天列还留得住，
 * 不用把三栏提前改成上下堆叠。
 */
const HISTORY_WIDTH = "min(420px, 36%)";
/** 三栏电脑列的默认份额：占总宽 30%，与参考稿的电脑列比例一致。 */
const THREE_PANE_DEFAULT = 30;
/** 三栏时电脑列的可用区间，保证聊天列始终还能放下一条消息气泡。 */
const THREE_PANE_MIN = 25;
const THREE_PANE_MAX = 45;
/** 两栏电脑列的默认份额与拖拽区间（44 / 56）。 */
const TWO_PANE_DEFAULT = 56;
const TWO_PANE_MIN = 35;
const TWO_PANE_MAX = 70;

const clamp = (value: number, min: number, max: number) =>
  Math.max(min, Math.min(max, value));

/**
 * 聊天区网格：聊天 |（历史）|（分隔条 + 电脑）。
 *
 * 历史面板是网格里的一列，不是浮层，所以不会再压住桌面画面。历史列固定
 * 420px，拖拽只调电脑列、聊天列吃剩余——分隔条始终是真的能拖的。
 */
export function AssistantComputerPane({ open, onClose, children, botId, historyOpen = false, history }: {
  open: boolean; onClose: () => void; children: ReactNode; botId?: string | null;
  historyOpen?: boolean; history?: ReactNode;
}) {
  const container = useRef<HTMLDivElement>(null);
  // 两栏和三栏各记各的电脑列宽度：关掉历史回到两栏时用回用户上次的两栏宽度
  // （默认 56），不会被三栏默认 30 拖成一条窄条；反之亦然。
  const [twoPaneWidth, setTwoPaneWidth] = useState(TWO_PANE_DEFAULT);
  const [threePaneComputer, setThreePaneComputer] = useState(THREE_PANE_DEFAULT);

  const computerShare = historyOpen
    ? clamp(threePaneComputer, THREE_PANE_MIN, THREE_PANE_MAX)
    : clamp(twoPaneWidth, TWO_PANE_MIN, TWO_PANE_MAX);

  let gridTemplateColumns: string | undefined;
  if (open && historyOpen) {
    // 历史列 min(420px, 36%)；电脑列按总宽百分比，聊天吃剩余。
    gridTemplateColumns = `minmax(240px, 1fr) ${HISTORY_WIDTH} 6px minmax(0, ${computerShare}%)`;
  } else if (open) {
    gridTemplateColumns = `minmax(280px, ${100 - computerShare}fr) 6px minmax(0, ${computerShare}fr)`;
  } else if (historyOpen) {
    // 只开历史时同样用抽屉宽度，不再走 46/54 折算。
    gridTemplateColumns = `minmax(280px, 1fr) ${HISTORY_WIDTH}`;
  }

  // 拖拽只调电脑列：三栏时改 threePaneComputer（历史恒为 420px，聊天吃剩余），
  // 两栏时改 twoPaneWidth。两种模式的宽度互不影响。
  const nudge = (delta: number) => {
    if (historyOpen) {
      setThreePaneComputer(value => clamp(value + delta, THREE_PANE_MIN, THREE_PANE_MAX));
    } else {
      setTwoPaneWidth(value => clamp(value + delta, TWO_PANE_MIN, TWO_PANE_MAX));
    }
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
          if (!bounds) return;
          const share = (bounds.right - event.clientX) / bounds.width * 100;
          if (historyOpen) setThreePaneComputer(clamp(share, THREE_PANE_MIN, THREE_PANE_MAX));
          else setTwoPaneWidth(clamp(share, TWO_PANE_MIN, TWO_PANE_MAX));
        }}
        onPointerUp={event => { if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId); }} />
      <aside id="assistant-computer-pane" className={styles.pane}><ComputerDesktop key={botId ?? "primary"} embedded onClose={onClose} botId={botId} /></aside>
    </>}
  </div>;
}
