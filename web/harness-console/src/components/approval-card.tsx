"use client";

import { useEffect, useId, useRef, useState } from "react";
import styles from "./approval-card.module.css";
import type { ApprovalDecision } from "../lib/harness-server";

export interface ApprovalDetails {
  approval_id: string;
  run_id: string;
  tool_call_id?: string;
  reason?: string;
  expires_at?: string;
  tool_name?: string;
  argument_summary?: Record<string, unknown>;
  sandbox_provider?: string;
  sandbox_isolation?: string;
  policy_rule?: string;
  risk?: string;
}

export function approvalLabel(status: string) {
  return status === "pending" ? "等待人工审批" : status;
}

export function formatApprovalReason(reason?: string) {
  const text = reason?.trim();
  if (text === "matched policy rule sensitive-memory-review")
    return "本次操作涉及敏感内容的长期记忆，需要你确认。";
  if (text?.startsWith("matched policy rule "))
    return "平台策略要求确认本次操作。";
  return text || "此操作需要你确认后才能继续。";
}

const riskLabels: Record<string, string> = {
  low: "低",
  medium: "中",
  high: "高",
};

export function approvalContextRows(details: ApprovalDetails) {
  const summary = details.argument_summary ?? {};
  const action = [
    "command",
    "file_path",
    "path",
    "query",
    "url",
    "description",
  ]
    .map((key) => summary[key])
    .find((value) => typeof value === "string");
  const rows: Array<{ label: string; value: string }> = [];
  if (details.tool_name) rows.push({ label: "工具", value: details.tool_name });
  if (typeof action === "string") rows.push({ label: "操作", value: action });
  if (details.sandbox_provider || details.sandbox_isolation) {
    rows.push({
      label: "环境",
      value: [details.sandbox_provider, details.sandbox_isolation]
        .filter(Boolean)
        .join(" · "),
    });
  }
  if (details.risk) {
    rows.push({ label: "风险", value: riskLabels[details.risk] ?? details.risk });
  }
  if (details.policy_rule) {
    rows.push({ label: "策略", value: details.policy_rule });
  }
  return rows;
}

export function ApprovalCard({ details, complete, onDecision }: {
  details: ApprovalDetails;
  complete: boolean;
  onDecision: (value: ApprovalDecision) => Promise<void>;
}) {
  const titleId = useId();
  const [pending, setPending] = useState<ApprovalDecision | null>(null);
  const [decision, setDecision] = useState<ApprovalDecision | null>(null);
  const [error, setError] = useState("");
  const submitting = useRef<string | null>(null);
  const currentId = useRef(details.approval_id);
  currentId.current = details.approval_id;
  const approve = useRef<HTMLButtonElement>(null);
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    submitting.current = null;
    setPending(null); setDecision(null); setError(""); setNow(Date.now());
    if (!complete && !document.activeElement?.closest('input, textarea, [contenteditable="true"]'))
      approve.current?.focus({ preventScroll: true });
  }, [details.approval_id]);
  useEffect(() => {
    if (!details.expires_at || complete) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [details.expires_at, complete]);
  const expired = Boolean(details.expires_at && Date.parse(details.expires_at) <= now);

  async function decide(value: ApprovalDecision) {
    if (submitting.current || complete || decision || expired
      || Boolean(details.expires_at && Date.parse(details.expires_at) <= Date.now())) return;
    const requestId = details.approval_id;
    submitting.current = requestId; setError(""); setPending(value);
    try {
      await onDecision(value);
      if (currentId.current === requestId) setDecision(value);
    } catch (cause) {
      if (currentId.current === requestId)
        setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      if (currentId.current === requestId) { submitting.current = null; setPending(null); }
    }
  }

  const settled = complete || decision !== null || expired;
  const contextRows = approvalContextRows(details);
  const action = contextRows.find(row => row.label === "操作")?.value;
  const title = details.tool_name === "Bash" ? "允许运行这条命令？"
    : ["Write", "Edit"].includes(details.tool_name ?? "") ? "允许修改这个文件？"
    : "允许执行这个操作？";
  const status = decision === "approved" ? "已允许本次操作，运行正在恢复"
    : decision === "rejected" ? "已拒绝本次操作"
    : expired && !complete ? "审批已过期" : "审批已处理";
  return (
    <section className={styles.card} aria-labelledby={titleId} data-approval-surface="codex">
      <div className={styles.body}>
        <div className={styles.header}>
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 4 7v5c0 5 8 9 8 9s8-4 8-9V7l-8-4Z"/><path d="m8.5 12 2.5 2.5 4.5-5"/></svg>
          <span>{details.tool_name || "操作确认"}</span>
          {details.risk === "high" && <span className={styles.elevated}>较高风险</span>}
        </div>
        <h3 id={titleId}>{title}</h3>
        <p className={styles.scope}>允许仅对本次调用生效。</p>
        <div className={styles.reason}><span>原因</span><p>{formatApprovalReason(details.reason)}</p></div>
        {action && <div className={styles.action}><span>{details.tool_name === "Bash" ? "命令" : "操作"}</span><pre><code>{action}</code></pre></div>}
        <details className={styles.details}>
          <summary>查看操作详情</summary>
          <dl>{contextRows.filter(row => row.label !== "操作").map(row => <div key={row.label}><dt>{row.label}</dt><dd>{row.value}</dd></div>)}</dl>
        </details>
        {error && <p className={styles.error} role="alert">{error}</p>}
      </div>
      <div className={styles.footer}>
        {settled ? <span className={styles.status} role="status">{status}</span> : <div className={styles.actions}>
          <button className={styles.deny} type="button" disabled={pending !== null} onClick={() => void decide("rejected")}>{pending === "rejected" ? "正在拒绝…" : "拒绝"}</button>
          <button ref={approve} className={styles.allow} type="button" disabled={pending !== null} onClick={() => void decide("approved")}>{pending === "approved" ? "正在允许…" : "允许一次"}</button>
        </div>}
      </div>
    </section>
  );
}
