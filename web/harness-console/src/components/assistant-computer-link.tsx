"use client";

import { useEffect, useRef, type ReactNode } from "react";

export function AssistantComputerLink({ open, onToggle, ready = false, className, children }: {
  open: boolean; onToggle: () => void; ready?: boolean; className?: string; children?: ReactNode;
}) {
  const endpoint = useRef<string | null>(null);
  useEffect(() => {
    if (!ready) return;
    const controller = new AbortController();
    // Low-cost capability discovery happens after the conversation is usable.
    // It does not mount noVNC, open a socket, or acquire desktop control.
    const timer = setTimeout(() => {
      void fetch("/api/computer/status", { cache: "no-store", signal: controller.signal })
        .then(async response => {
          if (response.ok) endpoint.current = (await response.json() as {desktop_url?: string}).desktop_url ?? null;
        }).catch(() => undefined);
    }, 1000);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [ready]);
  async function toggle() {
    if (window.location.protocol === "https:") { onToggle(); return; }
    // Resolve a secure viewer origin only after the user opens the computer.
    try {
      let href = endpoint.current;
      if (!href) {
        const response = await fetch("/api/computer/status", { cache: "no-store" });
        if (!response.ok) return;
        href = (await response.json() as {desktop_url?: string}).desktop_url ?? null;
      }
      const url = new URL(href ?? "/assistant/computer", window.location.href);
      if (url.hostname !== window.location.hostname || url.protocol !== "https:") return;
      url.pathname = "/assistant";
      const bot = new URLSearchParams(window.location.search).get("bot");
      url.search = new URLSearchParams({ computer: "open", ...(bot ? { bot } : {}) }).toString();
      window.location.assign(url.href);
    } catch { /* The conversation remains usable if the viewer is unavailable. */ }
  }
  return <button type="button" className={className ?? "header-icon-button"} aria-label={open ? "收起电脑" : "打开电脑"} title={open ? "收起电脑" : "打开电脑"} aria-expanded={open} aria-controls="assistant-computer-pane" onClick={() => void toggle()}>
    {children ?? <svg viewBox="0 0 20 20" aria-hidden="true"><rect x="2.5" y="3" width="15" height="10" rx="1.5" /><path d="M7 17h6m-3-4v4" /></svg>}
  </button>;
}
