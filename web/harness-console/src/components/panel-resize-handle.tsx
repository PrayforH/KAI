"use client";

import { useEffect, useRef, useState } from "react";

const panels = {
  build: { min: 320, max: 720, initial: 360, side: "left", label: "构建对话" },
  assets: { min: 240, max: 640, initial: 300, side: "left", label: "智能体资产" },
  sidebar: { min: 220, max: 380, initial: 264, side: "left", label: "左侧导航栏" },
  builder: { min: 340, max: 680, initial: 420, side: "right", label: "构建助手" },
  rail: { min: 280, max: 520, initial: 300, side: "right", label: "任务工作区" },
} as const;

/** Shared pointer and keyboard resizing; CSS further limits widths to the viewport. */
export function PanelResizeHandle({ panel }: { panel: keyof typeof panels }) {
  const config = panels[panel];
  const [width, setWidth] = useState<number>(config.initial);
  const drag = useRef<{ x: number; width: number } | null>(null);
  const property = `--preferred-${panel}-width`;
  const key = `agent-harness-${panel}-width`;
  function update(value: number) {
    const next = Math.round(Math.min(config.max, Math.max(config.min, value)));
    setWidth(next);
    document.documentElement.style.setProperty(property, `${next}px`);
    try { localStorage.setItem(key, String(next)); } catch { /* Storage is optional. */ }
  }
  useEffect(() => {
    try {
      const saved = Number(localStorage.getItem(key));
      if (Number.isFinite(saved) && saved >= config.min) update(saved);
    } catch { /* Use the default width when storage is unavailable. */ }
    return () => { drag.current = null; };
  // Panel identity is fixed for this handle's lifetime.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [panel]);

  return <div className="panel-resize-handle" data-panel={panel} data-side={config.side}
    role="separator" tabIndex={0} aria-label={`调整${config.label}宽度`}
    aria-orientation="vertical" aria-valuemin={config.min} aria-valuemax={config.max}
    aria-valuenow={width} aria-valuetext={`${width} 像素`}
    title="拖动调整宽度 · 方向键微调 · 双击恢复默认"
    onDoubleClick={() => update(config.initial)}
    onPointerDown={(event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      drag.current = { x: event.clientX, width: event.currentTarget.parentElement?.getBoundingClientRect().width || width };
      event.currentTarget.setPointerCapture(event.pointerId);
    }}
    onPointerMove={(event) => {
      if (!drag.current) return;
      update(drag.current.width + (event.clientX - drag.current.x) * (config.side === "left" ? 1 : -1));
    }}
    onPointerUp={(event) => {
      drag.current = null;
      if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
    }}
    onPointerCancel={() => { drag.current = null; }}
    onLostPointerCapture={() => { drag.current = null; }}
    onKeyDown={(event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      if (event.key === "Home") update(config.min);
      else if (event.key === "End") update(config.max);
      else update(width + (event.key === "ArrowRight" ? 1 : -1) * (config.side === "left" ? 1 : -1) * (event.shiftKey ? 32 : 8));
    }}
  />;
}
