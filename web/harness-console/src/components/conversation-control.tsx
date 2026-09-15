"use client";

import type { ButtonHTMLAttributes } from "react";
import styles from "./conversation-control.module.css";

type Action = "send" | "stop" | "pause" | "resume";
type Props = Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> & {
  action: Action;
  "aria-label": string;
};

/** Codex PromptInputSubmit: icon-sm (32px), a 16px icon, and a round button. */
export function ConversationControl({ action, className = "", title, ...props }: Props) {
  return <button {...props} type={props.type ?? "button"}
    title={title ?? props["aria-label"]}
    data-conversation-action={action}
    className={`${styles.control} ${className}`}>
    <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
      {action === "send" && <path d="M8 13V3M3 8l5-5 5 5" />}
      {action === "stop" && <rect x="3" y="3" width="10" height="10" rx="1.5" fill="currentColor" stroke="none" />}
      {action === "pause" && <path d="M5.5 3.5v9m5-9v9" strokeWidth="2.5" />}
      {action === "resume" && <path d="m5 3 7 5-7 5Z" fill="currentColor" stroke="none" />}
    </svg>
  </button>;
}
