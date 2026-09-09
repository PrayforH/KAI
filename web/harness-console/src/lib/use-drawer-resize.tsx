"use client";

import { useCallback, useEffect, useState, type PointerEvent as ReactPointerEvent } from "react";

export type DrawerResizeOptions = {
  /** Smallest width the drawer may shrink to. */
  min?: number;
  /** Largest width; also capped by the viewport. */
  max?: number;
  /** Width used the first time this drawer is opened. */
  initial?: number;
};

/**
 * Drag-to-resize for a right-side drawer. The width is clamped to
 * [min, max] and to the viewport, and remembered per drawer key.
 */
export function useDrawerResize(
  storageKey: string,
  { min = 360, max = 1080, initial = 560 }: DrawerResizeOptions = {},
) {
  const clamp = useCallback(
    (value: number) => {
      const viewportCap =
        typeof window === "undefined" ? max : Math.max(min, window.innerWidth - 220);
      return Math.round(Math.min(Math.min(max, viewportCap), Math.max(min, value)));
    },
    [max, min],
  );
  const [width, setWidth] = useState(() => clamp(initial));

  useEffect(() => {
    try {
      const raw = localStorage.getItem(`harness:drawer-width:${storageKey}`);
      const saved = raw ? Number.parseInt(raw, 10) : Number.NaN;
      if (Number.isFinite(saved)) setWidth(clamp(saved));
    } catch {
      /* storage unavailable: keep the default width */
    }
  }, [clamp, storageKey]);

  const startResize = useCallback(
    (event: ReactPointerEvent<HTMLElement>) => {
      event.preventDefault();
      const startX = event.clientX;
      const startWidth = width;
      const onMove = (moveEvent: PointerEvent) => {
        // Dragging the left edge leftwards widens the drawer.
        setWidth(clamp(startWidth + (startX - moveEvent.clientX)));
      };
      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        document.body.style.userSelect = "";
      };
      document.body.style.userSelect = "none";
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [clamp, width],
  );

  useEffect(() => {
    try {
      localStorage.setItem(`harness:drawer-width:${storageKey}`, String(width));
    } catch {
      /* storage unavailable: keep the in-memory width */
    }
  }, [storageKey, width]);

  return { width, startResize };
}

/** Left-edge grab handle for a resizable drawer. */
export function DrawerResizeHandle({
  onPointerDown,
  className,
}: {
  onPointerDown: (event: ReactPointerEvent<HTMLElement>) => void;
  className?: string;
}) {
  return (
    <div
      className={className}
      role="separator"
      aria-orientation="vertical"
      aria-label="调整抽屉宽度"
      title="拖动调整宽度"
      onPointerDown={onPointerDown}
    />
  );
}
