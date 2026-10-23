"use client";

import { useEffect, useId, useLayoutEffect, useRef, useState, type ComponentPropsWithoutRef, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import type { LinkPreview } from "../lib/link-preview";
import { writeTextToClipboard } from "../lib/clipboard";
import { CodexResponseIcon } from "./codex-response-icon";
import { FeedbackToast } from "./feedback-toast";
import styles from "./source-link-hover.module.css";

/** Local actions appear first; lingering reveals Codex’s asynchronous website preview. */
export function SourceLinkHover({ href, children, onPointerEnter, onPointerLeave, onFocus, onBlur, onClick, onKeyDown, ...props }:
  ComponentPropsWithoutRef<"a"> & { href: string }) {
  const anchor = useRef<HTMLAnchorElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const openTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const focusPanel = useRef(false);
  const reposition = useRef(() => {});
  const suppressFocus = useRef(false);
  const copying = useRef(false);
  const identity = useRef<string | null>(href);
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null);
  const [preview, setPreview] = useState<LinkPreview | null>(null);
  const [rich, setRich] = useState(false);
  const [loading, setLoading] = useState(false);
  const [notice, setNotice] = useState("");
  const [copyError, setCopyError] = useState(false);
  const id = useId();
  function cancelTimers() { clearTimeout(openTimer.current); clearTimeout(closeTimer.current); }
  function close() { cancelTimers(); setOpen(false); setPosition(null); }
  function show(immediate = false) {
    cancelTimers();
    if (immediate) setOpen(true);
    else openTimer.current = setTimeout(() => setOpen(true), 150);
  }
  function leave() {
    clearTimeout(openTimer.current);
    clearTimeout(closeTimer.current);
    closeTimer.current = setTimeout(() => {
      if (!anchor.current?.matches(":focus-visible") && !panel.current?.contains(document.activeElement)) close();
    }, 180);
  }
  function dismissAndRestore() {
    close();
    if (panel.current?.contains(document.activeElement)) {
      suppressFocus.current = true;
      anchor.current?.focus({ preventScroll: true });
      suppressFocus.current = false;
    }
  }
  useEffect(() => {
    identity.current = href;
    close(); setPreview(null); setRich(false);
    return () => { identity.current = null; cancelTimers(); };
  }, [href]);
  useEffect(() => {
    if (!open) { setRich(false); return; }
    const controller = new AbortController();
    let current = true;
    const preload = setTimeout(() => {
      setLoading(true);
      void fetch(`/api/link-preview?url=${encodeURIComponent(href)}`, { signal: controller.signal, credentials: "same-origin" })
        .then(response => response.ok ? response.json() : null)
        .then(data => { if (current) setPreview(data?.preview ?? null); })
        .catch(() => { if (current) setPreview(null); })
        .finally(() => { if (current) setLoading(false); });
    }, 50);
    const expand = setTimeout(() => setRich(true), 550);
    return () => { current = false; clearTimeout(preload); clearTimeout(expand); controller.abort(); };
  }, [open, href]);
  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const link = anchor.current?.getBoundingClientRect(), card = panel.current?.getBoundingClientRect();
      if (!link || !card) return;
      const left = Math.max(8, Math.min(link.left, document.documentElement.clientWidth - card.width - 8));
      const below = window.innerHeight - link.bottom;
      const preferredTop = below < 320 && link.top > below ? link.top - card.height - 6 : link.bottom + 6;
      const top = Math.max(8, Math.min(preferredTop, window.innerHeight - card.height - 8));
      setPosition({ left, top });
    };
    document.dispatchEvent(new CustomEvent("harness:link-hover-open", { detail: id }));
    const otherOpened = (event: Event) => { if ((event as CustomEvent<string>).detail !== id) close(); };
    document.addEventListener("harness:link-hover-open", otherOpened);
    place();
    reposition.current = place;
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(place) : null;
    if (panel.current) observer?.observe(panel.current);
    if (focusPanel.current) { focusPanel.current = false; panel.current?.querySelector<HTMLElement>("a, button")?.focus(); }
    const dismiss = (event: PointerEvent) => {
      if (!anchor.current?.contains(event.target as Node) && !panel.current?.contains(event.target as Node)) close();
    };
    const escape = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); dismissAndRestore(); }
    };
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape, true);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      observer?.disconnect();
      reposition.current = () => {};
      document.removeEventListener("harness:link-hover-open", otherOpened);
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", escape, true);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [open, href]);
  useLayoutEffect(() => {
    if (open) reposition.current();
  }, [open, rich, preview]);
  async function copy() {
    if (copying.current) return;
    copying.current = true;
    try {
      const copied = await writeTextToClipboard(href);
      if (identity.current !== href) return;
      setCopyError(!copied);
      setNotice(copied ? "链接已复制" : "复制失败，请重试。");
      if (copied) dismissAndRestore();
    } catch {
      if (identity.current === href) { setCopyError(true); setNotice("复制失败，请重试。"); }
    } finally { copying.current = false; }
  }
  function navigateActions(event: KeyboardEvent<HTMLDivElement>) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
    const items = Array.from(panel.current?.querySelectorAll<HTMLElement>("a, button") ?? []);
    if (!items.length) return;
    event.preventDefault();
    const index = items.indexOf(document.activeElement as HTMLElement);
    const next = event.key === "Home" ? 0 : event.key === "End" ? items.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + items.length) % items.length;
    items[next]?.focus();
  }
  return <>
    <a {...props} href={href} ref={anchor} aria-controls={open ? id : undefined}
      onPointerEnter={event => { onPointerEnter?.(event); if (!event.defaultPrevented && event.pointerType !== "touch") show(); }}
      onPointerLeave={event => { onPointerLeave?.(event); leave(); }}
      onFocus={event => { onFocus?.(event); if (!event.defaultPrevented && !suppressFocus.current) show(true); }}
      onBlur={event => { onBlur?.(event); if (!panel.current?.contains(event.relatedTarget)) leave(); }}
      onClick={event => { close(); onClick?.(event); }}
      onKeyDown={event => {
        onKeyDown?.(event);
        if (!event.defaultPrevented && event.key === "ArrowDown") {
          event.preventDefault(); focusPanel.current = true;
          if (open) { focusPanel.current = false; panel.current?.querySelector<HTMLElement>("a, button")?.focus(); }
          else show(true);
        }
      }}>{children}</a>
    {open && createPortal(<div id={id} ref={panel} role="toolbar" aria-label="链接操作" className={`${styles.toolbar} ${rich ? styles.preview : ""}`}
      style={{ ...position, visibility: position ? "visible" : "hidden" }}
      onPointerEnter={cancelTimers} onPointerLeave={leave} onFocus={cancelTimers}
      onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget) && !anchor.current?.contains(event.relatedTarget)) leave(); }}
      onKeyDown={navigateActions}>
      <div className={styles.actions}>
        {rich && <span className={styles.site}><SiteIcon href={href} /><span>{new URL(href).hostname}</span></span>}
        <a href={href} target="_blank" rel="noopener noreferrer" onClick={close} title={href}>
          {rich ? <>打开<CodexResponseIcon name="external" /></> : <><CodexResponseIcon name="external" />打开链接</>}
        </a>
        <button type="button" aria-label="复制链接" title="复制链接" onClick={() => void copy()}><CodexResponseIcon name="copy" />{!rich && "复制链接"}</button>
      </div>
      {rich && loading && <span className={styles.srOnly} role="status">正在加载预览…</span>}
      {rich && preview && <div className={styles.content}>
        {preview.title && <h3>{preview.title}</h3>}
        {preview.description && <p>{preview.description}</p>}
      </div>}
    </div>, document.body)}
    <FeedbackToast message={notice} tone={copyError ? "error" : "info"} onDismiss={() => setNotice("")} />
  </>;
}

function SiteIcon({ href }: { href: string }) {
  return new URL(href).hostname === "github.com" ? <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 .8a11.2 11.2 0 0 0-3.54 21.83c.56.1.77-.24.77-.54v-2.08c-3.13.68-3.79-1.33-3.79-1.33-.51-1.3-1.25-1.65-1.25-1.65-1.02-.7.08-.68.08-.68 1.13.08 1.72 1.15 1.72 1.15 1 1.72 2.63 1.22 3.27.93.1-.73.4-1.22.71-1.5-2.5-.28-5.13-1.25-5.13-5.57 0-1.23.44-2.24 1.15-3.03-.12-.28-.5-1.43.11-2.99 0 0 .94-.3 3.08 1.15a10.74 10.74 0 0 1 5.6 0c2.14-1.45 3.08-1.15 3.08-1.15.61 1.56.23 2.71.11 3 .72.78 1.15 1.79 1.15 3.02 0 4.33-2.64 5.28-5.15 5.56.41.35.77 1.03.77 2.08v3.09c0 .3.2.65.78.54A11.2 11.2 0 0 0 12 .8Z" /></svg> : <svg viewBox="0 0 24 24" aria-hidden="true" className={styles.globe}><circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18"/></svg>;
}
