"use client";

import type { ReactNode } from "react";
import type { RunActivity } from "../lib/activity-schema";
import { GrokFace } from "./grok-activity";
import styles from "./bot-run-notice.module.css";

const labels: Record<string, string> = {
  failed: "回复中断", timed_out: "等待超时", rejected: "本次操作未获允许",
  cancelled: "已停止", succeeded: "未收到回复",
};

/** Bot lifecycle feedback belongs to one quiet chat row, never a task error panel. */
export function BotRunNotice({activity, incomplete, hasResponse, retry}: {
  activity?: RunActivity;
  incomplete: boolean;
  hasResponse: boolean;
  retry: ReactNode;
}) {
  const terminal = activity?.items.findLast(item => /^run\.(failed|timed_out|rejected|cancelled|succeeded)$/.test(item.event_type));
  const status = terminal?.event_type.slice(4) ?? activity?.status;
  const label = status && labels[status] || (incomplete ? "回复中断" : "");
  if (!label || (!incomplete && hasResponse && status === "succeeded")) return null;
  return <div className={styles.notice} role="status" aria-label="回复状态">
    <GrokFace state="idle" size={20} />
    <span>{label}</span>
    {status !== "cancelled" && retry}
  </div>;
}
