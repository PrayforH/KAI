"use client";

import { useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import styles from "./action-menu-popover.module.css";

/** Portal keeps project resource and task-row menus outside scroll clipping. */
export function ActionMenuPopover({ anchor, label, onClose, children, className = "", align = "end" }: {
  anchor: RefObject<HTMLButtonElement | null>;
  label: string;
  onClose: () => void;
  children: ReactNode;
  className?: string;
  align?: "start" | "end";
}) {
  const panel = useRef<HTMLDivElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const [position, setPosition] = useState({ left: 8, top: 8 });
  useLayoutEffect(() => {
    const place = () => {
      const button = anchor.current?.getBoundingClientRect();
      const menu = panel.current?.getBoundingClientRect();
      if (!button || !menu) return;
      setPosition({
        left: Math.max(8, Math.min(align === "start" ? button.left : button.right - menu.width, window.innerWidth - menu.width - 8)),
        top: Math.max(8, Math.min(button.bottom + 6, window.innerHeight - menu.height - 8)),
      });
    };
    place();
    panel.current?.querySelector<HTMLElement>('[role="menuitem"]:not(:disabled)')?.focus();
    const dismiss = (event: PointerEvent) => {
      if (!panel.current?.contains(event.target as Node) && !anchor.current?.contains(event.target as Node)) closeRef.current();
    };
    const scroll = (event: Event) => {
      if (!panel.current?.contains(event.target as Node)) closeRef.current();
    };
    document.addEventListener("pointerdown", dismiss);
    window.addEventListener("resize", place);
    window.addEventListener("scroll", scroll, true);
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", scroll, true);
    };
  }, [anchor, align]);
  return createPortal(<div ref={panel} role="menu" aria-label={label}
    className={`${styles.menu} ${className}`} style={position}
    onKeyDown={(event) => {
      if (event.defaultPrevented) return;
      if (event.key === "Escape") {
        event.preventDefault(); event.stopPropagation(); closeRef.current(); anchor.current?.focus();
      } else if (event.key === "Tab") {
        closeRef.current(); anchor.current?.focus();
      } else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
        const items = Array.from(panel.current?.querySelectorAll<HTMLElement>('[role="menuitem"]:not(:disabled)') ?? []).filter(item => item.closest('[role="menu"]') === panel.current);
        if (!items.length) return;
        event.preventDefault();
        const index = items.indexOf(document.activeElement as HTMLElement);
        const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + items.length) % items.length;
        items[next]?.focus();
      }
    }}>{children}</div>, document.body);
}
