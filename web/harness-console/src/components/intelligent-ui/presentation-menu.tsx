"use client";

import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { CodexResponseIcon } from "../codex-response-icon";
import { uiPath } from "./client";
import type { Presentation } from "./schema";

/** Secondary document controls stay out of the tool's working surface. */
export function PresentationMenu({ presentation }: { presentation: Presentation }) {
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState<{ left: number; top: number }>();
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const focusLink = useRef(false);
  const id = useId();
  const url = `${uiPath(presentation.presentation_id)}/html?revision=${presentation.revision}`;

  useLayoutEffect(() => {
    if (!open) return;
    const anchor = trigger.current?.getBoundingClientRect();
    const bounds = panel.current?.getBoundingClientRect();
    if (!anchor || !bounds) return;
    setPosition({
      left: Math.max(8, Math.min(anchor.right - bounds.width, window.innerWidth - bounds.width - 8)),
      top: Math.max(8, anchor.bottom + bounds.height + 8 < window.innerHeight
        ? anchor.bottom + 4 : anchor.top - bounds.height - 4),
    });
  }, [open]);

  useLayoutEffect(() => {
    if (!open || !position || !focusLink.current) return;
    panel.current?.querySelector("a")?.focus();
    focusLink.current = false;
  }, [open, position]);

  useEffect(() => {
    if (!open) return;
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !trigger.current?.contains(event.target)
        && !panel.current?.contains(event.target)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault(); setOpen(false); trigger.current?.focus();
    };
    const moved = (event: Event) => {
      if (!(event.target instanceof Node) || !panel.current?.contains(event.target)) setOpen(false);
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    window.addEventListener("scroll", moved, true);
    window.addEventListener("resize", moved);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
      window.removeEventListener("scroll", moved, true);
      window.removeEventListener("resize", moved);
    };
  }, [open]);

  return <>
    <button ref={trigger} type="button" className="intelligent-ui-menu-trigger"
      aria-label="工具选项" title="工具选项" aria-haspopup="dialog"
      aria-expanded={open} aria-controls={open ? id : undefined}
      onClick={() => { setPosition(undefined); setOpen(value => !value); }}
      onKeyDown={event => {
        if (event.key !== "ArrowDown") return;
        event.preventDefault();
        if (open) panel.current?.querySelector("a")?.focus();
        else { focusLink.current = true; setOpen(true); }
      }}><CodexResponseIcon name="more" /></button>
    {open && createPortal(<div ref={panel} id={id} role="dialog" aria-label="工具信息"
      className="intelligent-ui-menu-panel" style={{ ...position, visibility: position ? "visible" : "hidden" }}
      onBlur={event => {
        if (event.relatedTarget instanceof Node && !event.currentTarget.contains(event.relatedTarget)
          && !trigger.current?.contains(event.relatedTarget)) setOpen(false);
      }}>
      <a className="intelligent-ui-menu-link" href={url} target="_blank" rel="noopener noreferrer"
        onClick={() => setOpen(false)}><CodexResponseIcon name="external" /><span>在新标签页打开</span></a>
      <div className="intelligent-ui-menu-info">
        <h3>关于此工具</h3>
        <p>{presentation.fallback_text}</p>
        <span>{presentation.decision.origin === "shape" ? "来源：本轮工具数据" : "来源：本轮回答"} · 版本 {presentation.revision}</span>
        {presentation.snapshot_hash && <details><summary>查看数据标识</summary>
          <code>{presentation.snapshot_hash}</code></details>}
      </div>
    </div>, document.body)}
  </>;
}
