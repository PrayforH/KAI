"use client";

import { useEffect, useState } from "react";

export function AssistantComputerLink({ open, onToggle }: { open: boolean; onToggle: () => void }) {
  const [href, setHref] = useState<string | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    void fetch("/api/computer/status", { cache: "no-store", signal: controller.signal })
      .then(async response => { if (response.ok) {
        const status = await response.json() as { desktop_url?: string };
        const url = new URL(status.desktop_url ?? "/assistant/computer", window.location.href);
        if (url.hostname === window.location.hostname && url.protocol === "https:") setHref(url.href);
      } })
      .catch(() => undefined);
    return () => controller.abort();
  }, []);
  return href ? <button type="button" className="header-icon-button" aria-label={open ? "收起电脑" : "打开电脑"} title={open ? "收起电脑" : "打开电脑"} aria-expanded={open} aria-controls="assistant-computer-pane" onClick={() => {
    if (window.location.protocol === "https:") onToggle();
    else {
      const url = new URL(href); url.pathname = "/assistant"; url.search = "?computer=open";
      window.location.assign(url.href);
    }
  }}>
    <svg viewBox="0 0 20 20" aria-hidden="true"><rect x="2.5" y="3" width="15" height="10" rx="1.5" /><path d="M7 17h6m-3-4v4" /></svg>
  </button> : null;
}
