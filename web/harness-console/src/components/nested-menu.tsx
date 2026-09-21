"use client";

import { useId, useLayoutEffect, useRef, useState, type ReactNode, type KeyboardEvent } from "react";

/** A submenu stays inside its parent's dismiss boundary, including mouse travel. */
export function NestedMenu({ label, icon, disabled = false, children }: {
  label: string; icon: ReactNode; disabled?: boolean; children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState({ left: 0, top: 0 });
  const root = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const id = useId();
  const closeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cancelClose = () => { if (closeTimer.current) clearTimeout(closeTimer.current); };
  useLayoutEffect(() => () => cancelClose(), []);
  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const anchor = root.current?.getBoundingClientRect();
      const menu = panel.current?.getBoundingClientRect();
      if (!anchor || !menu) return;
      const right = anchor.right + 4;
      const left = right + menu.width <= window.innerWidth - 8 ? right : anchor.left - menu.width - 4;
      setPosition({
        left: Math.max(8, Math.min(left, window.innerWidth - menu.width - 8)) - anchor.left,
        top: Math.max(8, Math.min(anchor.top, window.innerHeight - menu.height - 8)) - anchor.top,
      });
    };
    place(); window.addEventListener("resize", place);
    return () => window.removeEventListener("resize", place);
  }, [open]);
  const items = () => Array.from(panel.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)') ?? []);
  const focusFirst = () => window.requestAnimationFrame(() => items()[0]?.focus());
  function onKeyDown(event: KeyboardEvent) {
    if ((event.key === "Escape" || event.key === "ArrowLeft") && open) {
      event.preventDefault(); event.stopPropagation(); setOpen(false); trigger.current?.focus();
    } else if (event.target === trigger.current && ["ArrowRight", "ArrowDown"].includes(event.key)) {
      event.preventDefault(); if (!disabled) { setOpen(true); focusFirst(); }
    } else if (open && ["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
      event.preventDefault();
      const buttons = items(); const index = buttons.indexOf(document.activeElement as HTMLButtonElement);
      const next = event.key === "Home" ? 0 : event.key === "End" ? buttons.length - 1 : (index + (event.key === "ArrowDown" ? 1 : -1) + buttons.length) % buttons.length;
      buttons[next]?.focus();
    }
  }
  // Next delegates events at document level. Capture Escape before the parent
  // menu's native document listener so it closes only this submenu.
  return <div ref={root} className="nested-menu" onKeyDownCapture={onKeyDown}
    onMouseEnter={() => { cancelClose(); if (!disabled) setOpen(true); }}
    onMouseLeave={() => { cancelClose(); closeTimer.current = setTimeout(() => { if (!root.current?.contains(document.activeElement)) setOpen(false); }, 160); }}
    onBlur={(event) => { if (!event.currentTarget.contains(event.relatedTarget)) setOpen(false); }}>
    <button ref={trigger} type="button" role="menuitem" className="nested-menu-trigger" disabled={disabled}
      aria-haspopup="menu" aria-expanded={open} aria-controls={open ? id : undefined}
      onClick={() => { cancelClose(); setOpen(true); }}>
      {icon}<span>{label}</span><svg className="nested-menu-chevron" viewBox="0 0 16 16" aria-hidden="true"><path d="m6 3 5 5-5 5" /></svg>
    </button>
    {open && <div ref={panel} id={id} role="menu" aria-label={label} className="nested-menu-panel" style={position}>{children}</div>}
  </div>;
}
