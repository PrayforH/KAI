"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import styles from "./computer-desktop.module.css";

export function AssistantComputerLink() {
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
  return href ? <Link className={styles.entry} href={href}>
    <svg viewBox="0 0 20 20" aria-hidden="true"><rect x="2.5" y="3" width="15" height="10" rx="1.5" /><path d="M7 17h6m-3-4v4" /></svg>
    电脑
  </Link> : null;
}
