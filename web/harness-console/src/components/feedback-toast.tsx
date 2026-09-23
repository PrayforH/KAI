"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import styles from "./feedback-toast.module.css";

let viewport: HTMLDivElement | null = null;
let users = 0;
function acquireViewport() {
  if (!viewport?.isConnected) {
    viewport = document.createElement("div");
    viewport.className = styles.viewport;
    viewport.setAttribute("aria-label", "操作提示");
    document.body.append(viewport);
  }
  users++;
  return viewport;
}

/** Shared operation feedback; field validation and actionable conflicts stay inline. */
export function FeedbackToast({message, tone = "info", duration, onDismiss}: {
  message?: string | null; tone?: "info" | "error"; duration?: number; onDismiss?: () => void;
}) {
  const [target, setTarget] = useState<HTMLDivElement | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const dismissRef = useRef(onDismiss); dismissRef.current = onDismiss;
  useEffect(() => {
    if (!message) return;
    setTarget(acquireViewport());
    return () => { if (--users === 0) {viewport?.remove(); viewport = null;} };
  }, [Boolean(message)]);
  useEffect(() => {
    setDismissed(false);
    if (!message) return;
    const timeout = duration ?? (tone === "error" ? 0 : 4500);
    if (!timeout) return;
    const timer = setTimeout(() => {setDismissed(true); dismissRef.current?.();}, timeout);
    return () => clearTimeout(timer);
  }, [message, tone, duration]);
  if (!message || dismissed || !target) return null;
  return createPortal(<div className={styles.toast} role={tone === "error" ? "alert" : "status"} data-tone={tone}>
    <span>{message}</span>
    <button type="button" aria-label="关闭提示" onClick={() => {setDismissed(true); dismissRef.current?.();}}>×</button>
  </div>, target);
}
