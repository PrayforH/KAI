"use client";

import { type ComponentPropsWithoutRef, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useDialogFocus } from "../lib/use-dialog-focus";
import styles from "./web-source-image.module.css";

/** News images stay remote; a failed source remains a usable link. */
export function WebSourceImage({ src, alt, title }: ComponentPropsWithoutRef<"img">) {
  const [open, setOpen] = useState(false);
  const [failedSrc, setFailedSrc] = useState<string>();
  const panelRef = useRef<HTMLDivElement>(null);
  useDialogFocus({ open, panelRef, onEscape: () => setOpen(false) });
  if (typeof src !== "string" || !src) return null;
  let url: URL | undefined;
  try { url = new URL(src); } catch { /* Preserve relative attachment images. */ }
  if (!url || url.protocol !== "https:") {
    // Keep the existing renderer's behavior for uploads and local artifacts.
    // eslint-disable-next-line @next/next/no-img-element
    return <img src={src} alt={alt || ""} title={title} />;
  }
  if (url.username || url.password) return null;
  const description = alt || title || "来源图片";
  const failed = failedSrc === src;
  return (
    <span className={styles.frame}>
      {failed ? (
        <a href={src} target="_blank" rel="noopener noreferrer">图片无法加载 · 查看来源图片</a>
      ) : (
        <button type="button" className={styles.preview} aria-label={`放大图片：${description}`} onClick={() => setOpen(true)}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={src} alt={description} loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={() => setFailedSrc(src)} />
        </button>
      )}
      {open && createPortal(
        <div className={styles.backdrop} onClick={() => setOpen(false)}>
          <div className={styles.dialog} ref={panelRef} role="dialog" aria-modal="true" aria-label={description} onClick={event => event.stopPropagation()}>
            <div className={styles.toolbar}>
              <span>{description}</span>
              <a href={src} target="_blank" rel="noopener noreferrer">查看原图 ↗</a>
              <button type="button" aria-label="关闭图片预览" onClick={() => setOpen(false)}>关闭</button>
            </div>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={src} alt={description} referrerPolicy="no-referrer" />
          </div>
        </div>, document.body,
      )}
    </span>
  );
}
