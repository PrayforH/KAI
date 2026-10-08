"use client";

import { useEffect, useRef, useState, type PointerEvent } from "react";

const DEFAULT_WIDTH = 300;
const MIN_WIDTH = 280;
const COLLAPSE_WIDTH = 180;
const EXPAND_DISTANCE = 64;
const WIDTH_KEY = "agent-harness-rail-width";
const WIDTH_PROPERTY = "--preferred-rail-width";

/** The task drawer can snap closed or cover the conversation on release. */
export function RailResizeHandle({ expanded, onExpandedChange, onClose }: {
  expanded: boolean;
  onExpandedChange: (expanded: boolean) => void;
  onClose: () => void;
}) {
  const handle = useRef<HTMLDivElement>(null);
  const drag = useRef<{ pointerId: number; x: number; width: number; available: number; next: number; shell: HTMLElement } | null>(null);
  const [width, setWidth] = useState(DEFAULT_WIDTH);
  const [available, setAvailable] = useState(DEFAULT_WIDTH);

  function measure() {
    const rail = handle.current?.parentElement;
    const content = rail?.closest(".workspace-stage")?.querySelector(".task-content-shell");
    return Math.max(MIN_WIDTH + EXPAND_DISTANCE, Math.round(
      rail && content ? rail.getBoundingClientRect().right - content.getBoundingClientRect().left : window.innerWidth / 2,
    ));
  }
  function save(value: number, limit = measure()) {
    const next = Math.round(Math.max(MIN_WIDTH, Math.min(limit - EXPAND_DISTANCE, value)));
    setWidth(next);
    document.documentElement.style.setProperty(WIDTH_PROPERTY, `${next}px`);
    try { localStorage.setItem(WIDTH_KEY, String(next)); } catch { /* Storage is optional. */ }
  }
  function clearDrag() {
    drag.current?.shell.removeAttribute("data-rail-resizing");
    drag.current?.shell.style.removeProperty("--drag-rail-width");
    drag.current = null;
  }
  useEffect(() => {
    const resize = () => {
      clearDrag();
      const limit = measure();
      setAvailable(limit);
      let saved = DEFAULT_WIDTH;
      try { const value = Number(localStorage.getItem(WIDTH_KEY)); if (Number.isFinite(value) && value >= MIN_WIDTH) saved = value; } catch { /* Use the default. */ }
      save(saved, limit);
    };
    resize();
    window.addEventListener("resize", resize);
    return () => { window.removeEventListener("resize", resize); clearDrag(); };
    // The handle is mounted only while the drawer is open.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function move(event: PointerEvent<HTMLDivElement>) {
    const current = drag.current;
    if (!current || current.pointerId !== event.pointerId) return;
    current.next = current.width + current.x - event.clientX;
    current.shell.style.setProperty("--drag-rail-width", `${Math.round(Math.max(80, Math.min(current.available, current.next)))}px`);
  }
  function release(event: PointerEvent<HTMLDivElement>) {
    clearDrag();
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  }

  return <div ref={handle} className="panel-resize-handle" data-panel="rail" data-side="right"
    role="separator" tabIndex={0} aria-label="调整任务工作区宽度" aria-orientation="vertical"
    aria-valuemin={0} aria-valuemax={available} aria-valuenow={expanded ? available : width}
    aria-valuetext={expanded ? "占满对话区" : `${width} 像素`}
    title="拖动调整宽度，向左展开，向右收起 · 双击恢复默认"
    onDoubleClick={() => { save(DEFAULT_WIDTH); onExpandedChange(false); }}
    onPointerDown={(event) => {
      if (event.button !== 0 || !event.isPrimary || drag.current) return;
      const rail = event.currentTarget.parentElement;
      const shell = rail?.closest<HTMLElement>(".console-shell");
      if (!rail || !shell) return;
      event.preventDefault();
      event.currentTarget.focus({ preventScroll: true });
      const currentWidth = rail.getBoundingClientRect().width;
      const limit = measure();
      setAvailable(limit);
      drag.current = { pointerId: event.pointerId, x: event.clientX, width: currentWidth, available: limit, next: currentWidth, shell };
      shell.style.setProperty("--drag-rail-width", `${currentWidth}px`);
      shell.setAttribute("data-rail-resizing", "true");
      event.currentTarget.setPointerCapture(event.pointerId);
    }}
    onPointerMove={move}
    onPointerUp={(event) => {
      const current = drag.current;
      if (!current || current.pointerId !== event.pointerId) return;
      // Include the release position even if the browser coalesced the last move.
      move(event);
      const next = current.next;
      release(event);
      if (Math.abs(next - current.width) < 4) return;
      if (next <= COLLAPSE_WIDTH) { onExpandedChange(false); onClose(); }
      else if (next >= current.available - EXPAND_DISTANCE) onExpandedChange(true);
      else { save(next, current.available); onExpandedChange(false); }
    }}
    onPointerCancel={(event) => { if (drag.current?.pointerId === event.pointerId) release(event); }}
    onLostPointerCapture={clearDrag}
    onKeyDown={(event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End", "Escape"].includes(event.key)) return;
      event.preventDefault();
      if (event.key === "Escape") {
        const pointerId = drag.current?.pointerId;
        clearDrag();
        if (pointerId !== undefined && event.currentTarget.hasPointerCapture(pointerId)) event.currentTarget.releasePointerCapture(pointerId);
        return;
      }
      if (event.key === "Home") { onExpandedChange(false); onClose(); }
      else if (event.key === "End") onExpandedChange(true);
      else { save((expanded ? measure() : width) + (event.key === "ArrowLeft" ? 1 : -1) * (event.shiftKey ? 32 : 8)); onExpandedChange(false); }
    }}
  />;
}
