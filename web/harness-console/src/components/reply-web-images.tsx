"use client";

import { useEffect, useRef, useState } from "react";
import { ConversationImageViewer, type ConversationImage } from "./conversation-image-viewer";

export function ReplyWebImages({ images }: { images: readonly ConversationImage[] }) {
  const [index, setIndex] = useState<number | null>(null);
  const [failed, setFailed] = useState<string[]>([]);
  const strip = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState({ left: false, right: false });
  const updateEdges = () => {
    const element = strip.current;
    if (element) setEdges({ left: element.scrollLeft > 1, right: element.scrollLeft + element.clientWidth < element.scrollWidth - 1 });
  };
  useEffect(() => {
    updateEdges();
    const element = strip.current;
    if (!element || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(updateEdges);
    observer.observe(element);
    return () => observer.disconnect();
  }, [images.length]);
  if (!images.length) return null;
  return <section className="reply-web-images" aria-label="网页图片">
    <div className="reply-web-image-strip" ref={strip} onScroll={updateEdges}>
      {images.map((image, position) => <figure key={image.src}>
        {failed.includes(image.src) ? <a className="reply-web-image-fallback" href={image.sourceUrl ?? image.src} target="_blank" rel="noopener noreferrer">查看来源图片 ↗</a> : <button type="button" aria-label={`查看图片 ${position + 1}：${image.title}`} onClick={() => setIndex(position)}>
          {/* Remote public image, loaded without a backend fetch or proxy. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={image.src} alt={image.title} loading="lazy" decoding="async" referrerPolicy="no-referrer" onError={() => setFailed(current => [...current, image.src])} />
        </button>}
        <figcaption title={image.title}>{image.sourceUrl ? <a href={image.sourceUrl} target="_blank" rel="noopener noreferrer">{new URL(image.sourceUrl).hostname} ↗</a> : new URL(image.src).hostname}</figcaption>
      </figure>)}
    </div>
    {(edges.left || edges.right) && <nav className="reply-web-image-controls" aria-label="浏览网页图片">
      <button type="button" disabled={!edges.left} aria-label="向左浏览图片" onClick={() => strip.current?.scrollBy({left: -336, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth"})}>‹</button>
      <button type="button" disabled={!edges.right} aria-label="向右浏览图片" onClick={() => strip.current?.scrollBy({left: 336, behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth"})}>›</button>
    </nav>}
    {index !== null && <ConversationImageViewer images={images} index={index} onSelect={setIndex} onClose={() => setIndex(null)} />}
  </section>;
}
