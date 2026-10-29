"use client";

import { type ComponentPropsWithoutRef, useState } from "react";
import { ConversationImageViewer } from "./conversation-image-viewer";
import { publicImageUrl } from "../lib/reply-web-images";
import styles from "./web-source-image.module.css";

/** Compact answer images; only the viewer uses the original proportions. */
export function WebSourceImage({ src, alt, title }: ComponentPropsWithoutRef<"img">) {
  const [open, setOpen] = useState(false);
  const [failedSrc, setFailedSrc] = useState<string>();
  if (typeof src !== "string" || !src) return null;
  const local = /^\/api\/(harness\/artifacts|input-artifacts)\//.test(src);
  const url = local ? src : publicImageUrl(src);
  if (!url) return null;
  const description = alt || title || "来源图片";
  return <span className={styles.frame}>
    {failedSrc === src ? <a href={url} target="_blank" rel="noopener noreferrer">图片无法加载 · 查看来源图片 ↗</a> : <button type="button" className={styles.preview} aria-label={`放大图片：${description}`} onClick={() => setOpen(true)}>
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={url} alt={description} loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={() => setFailedSrc(src)} />
    </button>}
    {open && <ConversationImageViewer images={[{src: url, title: description, sourceUrl: local ? undefined : url}]} index={0} onSelect={() => {}} onClose={() => setOpen(false)} />}
  </span>;
}
