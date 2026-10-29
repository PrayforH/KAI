"use client";

import type { ComponentPropsWithoutRef } from "react";
import { CodexResponseIcon } from "./codex-response-icon";

/** Shared compact controls for answer cards, with a stable accessible name. */
export function CardActionButton({ action, label, copied = false, copyState, className = "", ...props }: {
  action: "copy" | "download";
  label: string;
  copied?: boolean;
  copyState?: "idle" | "copied" | "failed";
} & Omit<ComponentPropsWithoutRef<"button">, "children">) {
  return <button {...props} type="button" className={`card-icon-action ${className}`}
    aria-label={label} title={copyState === "failed" ? "复制失败，请手动选择" : copied ? "已复制" : label} data-copy-state={action === "copy" ? (copyState ?? (copied ? "copied" : "idle")) : undefined}>
    <CodexResponseIcon name={copied ? "check" : action} />
    {action === "copy" && <span className="card-action-feedback" role="status" aria-live="polite">{copyState === "failed" ? "复制失败，请手动选择" : copied ? "已复制" : ""}</span>}
  </button>;
}


export function CardDownloadLink({ label, className = "", ...props }: {
  label: string;
} & Omit<ComponentPropsWithoutRef<"a">, "children">) {
  return <a {...props} className={`card-icon-action ${className}`} aria-label={label} title={label}>
    <CodexResponseIcon name="download" />
  </a>;
}
