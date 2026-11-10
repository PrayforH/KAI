"use client";

import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { useDialogFocus } from "../lib/use-dialog-focus";
import { CodexResponseIcon } from "./codex-response-icon";
import {
  RailFilePreview,
  artifactContentUrl,
  artifactDownloadUrl,
  type PreviewTarget,
} from "./rail-file-preview";
import styles from "./preview-dialog.module.css";

/** One overlay for the assistant surface: title, file actions and the preview
 * fill a centered panel instead of stretching the conversation inline. */
export function PreviewDialog({ target, onClose }: { target: PreviewTarget; onClose: () => void }) {
  const panel = useRef<HTMLElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  useDialogFocus({ open: true, panelRef: panel, initialFocusRef: close, onEscape: onClose });
  useEffect(() => {
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = overflow; };
  }, []);
  if (typeof document === "undefined") return null;
  return createPortal(
    <div
      className={styles.backdrop}
      onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}
    >
      <section
        ref={panel}
        className={styles.panel}
        role="dialog"
        aria-modal="true"
        aria-label={`预览 ${target.name}`}
      >
        <header className={styles.head}>
          <span className={styles.title} title={target.name}>{target.name}</span>
          <div className={styles.actions}>
            <a
              className={styles.action}
              href={artifactContentUrl(target)}
              target="_blank"
              rel="noreferrer"
              aria-label={`在新标签页打开 ${target.name}`}
              title="在新标签页打开"
            >
              <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
                <path d="M6.4 3.6H3.6v8.8h8.8V9.6" />
                <path d="M9.5 3.6h2.9v2.9" />
                <path d="M12.4 3.6 7.3 8.7" />
              </svg>
            </a>
            <a
              className={styles.action}
              href={artifactDownloadUrl(target)}
              download={target.name}
              aria-label={`下载 ${target.name}`}
              title={`下载 ${target.name}`}
            >
              <CodexResponseIcon name="download" />
            </a>
            <button
              ref={close}
              type="button"
              className={styles.close}
              onClick={onClose}
              aria-label="关闭预览"
              title="关闭预览"
            >
              <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
                <path d="M4.4 4.4l7.2 7.2M11.6 4.4l-7.2 7.2" />
              </svg>
            </button>
          </div>
        </header>
        <div className={styles.body}>
          <RailFilePreview key={target.artifact_id} target={target} hideHeader />
        </div>
      </section>
    </div>,
    document.body,
  );
}
