import { useEffect, useState } from "react";

/** 消息正文/气泡上的标记：操作条只认它作为锚点。 */
export const MESSAGE_BODY_ATTRIBUTE = "data-message-body";

/** 气泡在滚动区里至少露出这么高，操作条才允许出现。 */
export const MIN_VISIBLE_ANCHOR_HEIGHT = 24;

export type VerticalEdges = { top: number; bottom: number };

/** 锚点被滚动容器裁切后，真正还能看到的纵向高度。 */
export function visibleHeightWithin(anchor: VerticalEdges, scroller: VerticalEdges): number {
  return Math.max(0, Math.min(anchor.bottom, scroller.bottom) - Math.max(anchor.top, scroller.top));
}

export function isMessageAnchorVisible(
  anchor: VerticalEdges,
  scroller: VerticalEdges,
  minVisibleHeight = MIN_VISIBLE_ANCHOR_HEIGHT,
): boolean {
  return visibleHeightWithin(anchor, scroller) >= minVisibleHeight;
}

/** jsdom / 首帧可能拿不到布局；拿不到时不拦截，避免误伤。 */
export function hasMeasuredGeometry(rect: VerticalEdges): boolean {
  return rect.top !== 0 || rect.bottom !== 0;
}

/** 最近的纵向滚动容器：用于判断弹层会不会被裁掉、气泡有没有露出。 */
export function findScrollParent(node: HTMLElement | null): HTMLElement | null {
  for (let current = node?.parentElement ?? null; current; current = current.parentElement) {
    const overflowY = getComputedStyle(current).overflowY;
    if (overflowY === "auto" || overflowY === "scroll") return current;
  }
  return null;
}

/**
 * 从操作条往上找本条消息的正文：第一个只含一个锚点的祖先就是它的消息容器。
 * 找到多个说明已经越过本条消息，宁可不拦截也不认错锚点。
 */
export function findMessageAnchor(from: HTMLElement | null): HTMLElement | null {
  for (let node = from?.parentElement ?? null; node; node = node.parentElement) {
    const bodies = node.querySelectorAll<HTMLElement>(`[${MESSAGE_BODY_ATTRIBUTE}]`);
    if (bodies.length === 1) return bodies[0];
    if (bodies.length > 1) return null;
    if (node === node.ownerDocument.body) return null;
  }
  return null;
}

/**
 * 锚点在滚动区内的可见高度是否 ≥24px。
 * 优先用 IntersectionObserver（root 收缩 24px）：每条消息一个观察者，只有跨过
 * 阈值才回调，不在滚动帧里读布局。环境不支持时才退回 scroll + rAF 的有界测量。
 */
export function useMessageAnchorVisibility(anchor: HTMLElement | null): boolean {
  const [visible, setVisible] = useState(true);

  useEffect(() => {
    if (!anchor) {
      setVisible(true);
      return;
    }
    const scroller = findScrollParent(anchor);
    if (!scroller) {
      setVisible(true);
      return;
    }

    if (typeof IntersectionObserver !== "undefined") {
      // 上下各收 24px：气泡在滚动区里露出不足 24px 就不会与内框相交。
      const observer = new IntersectionObserver(
        (entries) => {
          const entry = entries[entries.length - 1];
          if (!entry) return;
          const next = entry.isIntersecting;
          setVisible((current) => (current === next ? current : next));
        },
        {
          root: scroller,
          rootMargin: `-${MIN_VISIBLE_ANCHOR_HEIGHT}px 0px -${MIN_VISIBLE_ANCHOR_HEIGHT}px 0px`,
          threshold: 0,
        },
      );
      observer.observe(anchor);
      return () => observer.disconnect();
    }

    let frame = 0;
    let cancelled = false;
    const measure = () => {
      frame = 0;
      if (cancelled) return;
      const anchorRect = anchor.getBoundingClientRect();
      const scrollerRect = scroller.getBoundingClientRect();
      if (!hasMeasuredGeometry(anchorRect) || !hasMeasuredGeometry(scrollerRect)) {
        setVisible(true);
        return;
      }
      const next = isMessageAnchorVisible(anchorRect, scrollerRect);
      setVisible((current) => (current === next ? current : next));
    };
    const schedule = () => {
      if (frame) return;
      if (typeof requestAnimationFrame === "function") frame = requestAnimationFrame(measure);
      else measure();
    };

    measure();
    scroller.addEventListener("scroll", schedule, { passive: true });
    window.addEventListener("resize", schedule);
    let resizeObserver: ResizeObserver | null = null;
    if (typeof ResizeObserver !== "undefined") {
      resizeObserver = new ResizeObserver(schedule);
      resizeObserver.observe(anchor);
      resizeObserver.observe(scroller);
    }

    return () => {
      cancelled = true;
      if (frame) cancelAnimationFrame(frame);
      scroller.removeEventListener("scroll", schedule);
      window.removeEventListener("resize", schedule);
      resizeObserver?.disconnect();
    };
  }, [anchor]);

  return visible;
}
