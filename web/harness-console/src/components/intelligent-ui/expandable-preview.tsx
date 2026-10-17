"use client";

import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import styles from "./expandable-preview.module.css";

/** Promote the existing frame into the top layer; never remount its document. */
export function ExpandablePreview({ title, children }: {
  title: string;
  children: (control: ReactNode) => ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  const previous = useRef(false);
  const [expanded, setExpanded] = useState(false);

  useLayoutEffect(() => {
    const element = dialog.current;
    if (!element || previous.current === expanded) return;
    previous.current = expanded;
    element.close();
    if (expanded) element.showModal();
    else element.show();
    toggle.current?.focus();
  }, [expanded]);

  const label = expanded ? "收起 HTML 预览" : "展开 HTML 预览";
  return <dialog ref={dialog} open className={styles.shell} data-expanded={expanded}
    role={expanded ? "dialog" : "region"} aria-modal={expanded || undefined} aria-label={title}
    onCancel={event => { event.preventDefault(); setExpanded(false); }}>
    {children(<button ref={toggle} type="button" className={styles.toggle}
      title={label} aria-label={label} aria-expanded={expanded}
      onClick={() => setExpanded(value => !value)}>
      <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"
        strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        {expanded ? <path d="M7 3v4H3m10-4v4h4M3 13h4v4m10-4h-4v4" />
          : <path d="M7 3H3v4m10-4h4v4M3 13v4h4m10-4v4h-4" />}
      </svg>
    </button>)}
  </dialog>;
}
