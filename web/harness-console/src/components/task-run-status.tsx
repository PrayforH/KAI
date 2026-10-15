"use client";

import type { RunActivity } from "../lib/activity-schema";
import type { RunStreamSnapshot } from "../lib/run-stream-store";
import { taskRunNotice } from "../lib/task-run-notice";
import styles from "./task-run-status.module.css";

export function TaskRunStatus({ activity, stream, status }: {
  activity?: RunActivity;
  stream: RunStreamSnapshot;
  status?: string;
}) {
  const notice = taskRunNotice(activity, stream, status);
  if (!notice) return null;
  const label = <span className={styles.label} role="status" aria-live="polite">
    <span className={styles.dot} aria-hidden="true" />{notice.label}
  </span>;
  if (!notice.detail) return <span className={styles.status} data-tone={notice.tone}>{label}</span>;
  return <details className={styles.status} data-tone={notice.tone} key={notice.runId}>
    <summary aria-label={`${notice.label}，查看原因`}>{label}</summary>
    <div className={styles.detail}>
      <strong>{notice.label}</strong>
      <p>{notice.detail}</p>
      <button type="button" onClick={() => window.dispatchEvent(new CustomEvent("harness:open-trace"))}>查看运行详情</button>
    </div>
  </details>;
}
