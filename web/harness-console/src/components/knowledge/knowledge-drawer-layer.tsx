"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import styles from "./knowledge-drawer-layer.module.css";

/** Keep knowledge dialogs above the shell, outside its stacking contexts. */
export function KnowledgeDrawerLayer({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  const [ready, setReady] = useState(false);
  const layer = useRef<HTMLDivElement>(null);
  const close = useRef(onClose);
  close.current = onClose;
  useEffect(() => setReady(true), []);
  useEffect(() => {
    if (!ready || !layer.current) return;
    const previousFocus = document.activeElement as HTMLElement | null;
    const previousOverflow = document.body.style.overflow;
    const siblings = Array.from(document.body.children).filter((node): node is HTMLElement => node instanceof HTMLElement && node !== layer.current);
    const inert = siblings.map((node) => node.inert);
    siblings.forEach((node) => { node.inert = true; });
    document.body.style.overflow = "hidden";
    layer.current.querySelector<HTMLElement>('[aria-label="关闭"], [role="dialog"] button')?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopImmediatePropagation();
        close.current();
      } else if (event.key === "Tab") {
        const targets = Array.from(layer.current?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled):not([type="hidden"]), select:not(:disabled), textarea:not(:disabled), [tabindex="0"]') ?? []);
        const first = targets[0]; const last = targets.at(-1);
        if (!first || !last) return;
        if (!layer.current?.contains(document.activeElement) || (!event.shiftKey && document.activeElement === last)) {
          event.preventDefault(); first.focus();
        } else if (event.shiftKey && document.activeElement === first) {
          event.preventDefault(); last.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      siblings.forEach((node, index) => { node.inert = inert[index]; });
      document.body.style.overflow = previousOverflow;
      if (previousFocus?.isConnected) previousFocus.focus();
    };
  }, [ready]);
  return ready ? createPortal(<div ref={layer} className={styles.layer}>{children}</div>, document.body) : null;
}
