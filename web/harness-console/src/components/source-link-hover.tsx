"use client";

import { useEffect, useId, useLayoutEffect, useRef, useState, type ComponentPropsWithoutRef, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { writeTextToClipboard } from "../lib/clipboard";
import { CodexResponseIcon } from "./codex-response-icon";
import { FeedbackToast } from "./feedback-toast";
import styles from "./source-link-hover.module.css";

/** Codex's external-link hover toolbar: local actions, no remote preview fetch. */
export function SourceLinkHover({ href, children, onPointerEnter, onPointerLeave, onFocus, onBlur, onClick, onKeyDown, ...props }:
  ComponentPropsWithoutRef<"a"> & { href: string }) {
  const anchor = useRef<HTMLAnchorElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const openTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const closeTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const focusPanel = useRef(false);
  const suppressFocus = useRef(false);
  const copying = useRef(false);
  const identity = useRef<string | null>(href);
  const [open, setOpen] = useState(false);
  const [position, setPosition] = useState<{ left: number; top: number } | null>(null);
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
    close();
    return () => { identity.current = null; cancelTimers(); };
  }, [href]);
  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const link = anchor.current?.getBoundingClientRect(), card = panel.current?.getBoundingClientRect();
      if (!link || !card) return;
      const left = Math.max(8, Math.min(link.left, document.documentElement.clientWidth - card.width - 8));
      const preferredTop = link.top - card.height - 6;
      const top = Math.max(8, Math.min(preferredTop >= 8 ? preferredTop : link.bottom + 6, window.innerHeight - card.height - 8));
      setPosition({ left, top });
    };
    document.dispatchEvent(new CustomEvent("harness:link-hover-open", { detail: id }));
    const otherOpened = (event: Event) => { if ((event as CustomEvent<string>).detail !== id) close(); };
    document.addEventListener("harness:link-hover-open", otherOpened);
    place();
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
      document.removeEventListener("harness:link-hover-open", otherOpened);
      document.removeEventListener("pointerdown", dismiss);
      document.removeEventListener("keydown", escape, true);
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [open, href]);
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
    {open && createPortal(<div id={id} ref={panel} role="toolbar" aria-label="链接操作" className={styles.toolbar}
      style={{ ...position, visibility: position ? "visible" : "hidden" }}
      onPointerEnter={cancelTimers} onPointerLeave={leave} onFocus={cancelTimers}
      onBlur={event => { if (!event.currentTarget.contains(event.relatedTarget) && !anchor.current?.contains(event.relatedTarget)) leave(); }}
      onKeyDown={navigateActions}>
      <a href={href} target="_blank" rel="noopener noreferrer" onClick={close} title={href}>
        <CodexResponseIcon name="external" />打开链接
      </a>
      <button type="button" onClick={() => void copy()}><CodexResponseIcon name="copy" />复制链接</button>
    </div>, document.body)}
    <FeedbackToast message={notice} tone={copyError ? "error" : "info"} onDismiss={() => setNotice("")} />
  </>;
}
