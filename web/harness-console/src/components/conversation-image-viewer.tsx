"use client";

import { useEffect, useRef } from "react";
import { createPortal } from "react-dom";
import { useDialogFocus } from "../lib/use-dialog-focus";
import { CardDownloadLink } from "./card-action-button";

export interface ConversationImage {
  src: string;
  title: string;
  sourceUrl?: string;
  downloadHref?: string;
}

/** One original-size viewer shared by uploads, Markdown and search results. */
export function ConversationImageViewer({ images, index, onSelect, onClose }: {
  images: readonly ConversationImage[];
  index: number;
  onSelect: (index: number) => void;
  onClose: () => void;
}) {
  const panel = useRef<HTMLDivElement>(null);
  const close = useRef<HTMLButtonElement>(null);
  const image = images[index];
  useDialogFocus({ open: Boolean(image), panelRef: panel, initialFocusRef: close, onEscape: onClose });
  useEffect(() => {
    if (!image) return;
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = overflow; };
  }, [Boolean(image)]);
  if (!image) return null;
  return createPortal(
    <div className="image-lightbox" ref={panel} role="dialog" aria-modal="true" aria-label={`${image.title} 原图预览`}
      onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}
      onKeyDown={event => {
        if (images.length < 2) return;
        if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
          event.preventDefault();
          onSelect((index + (event.key === "ArrowLeft" ? -1 : 1) + images.length) % images.length);
        }
      }}>
      <header className="image-lightbox-toolbar">
        <span className="image-lightbox-title"><small>{index + 1} / {images.length}</small><strong>{image.title}</strong></span>
        <span className="image-lightbox-actions">
          {image.sourceUrl && <a href={image.sourceUrl} target="_blank" rel="noopener noreferrer">查看来源 ↗</a>}
          {image.downloadHref && <CardDownloadLink href={image.downloadHref} download={image.title} label="下载原图" />}
          <button ref={close} type="button" onClick={onClose} aria-label="关闭原图预览">×</button>
        </span>
      </header>
      <div className="image-lightbox-stage" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}>
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img key={image.src} src={image.src} alt={image.title} referrerPolicy="no-referrer" />
      </div>
      {images.length > 1 && <nav className="image-lightbox-navigation" aria-label="浏览图片">
        <button type="button" aria-label="上一张图片" onClick={() => onSelect((index - 1 + images.length) % images.length)}>‹</button>
        <button type="button" aria-label="下一张图片" onClick={() => onSelect((index + 1) % images.length)}>›</button>
      </nav>}
    </div>, document.body,
  );
}
