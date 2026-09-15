"use client";

import { useEffect, useRef, useState } from "react";
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
  return reason?.trim() || "此操作需要你确认后才能继续。";
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

export function ApprovalCard({
  details,
  complete,
  onDecision,
}: {
  details: ApprovalDetails;
  complete: boolean;
  onDecision: (value: ApprovalDecision) => Promise<void>;
}) {
  const [pending, setPending] = useState<ApprovalDecision | null>(null);
  const [decision, setDecision] = useState<ApprovalDecision | null>(null);
  const [error, setError] = useState("");
  const submitting = useRef(false);
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    if (!details.expires_at || complete) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [details.expires_at, complete]);
  const expired = Boolean(details.expires_at && Date.parse(details.expires_at) <= now);

  async function decide(value: ApprovalDecision) {
    if (submitting.current || complete || decision || expired) return;
    submitting.current = true;
    setError("");
    setPending(value);
    try {
      await onDecision(value);
      setDecision(value);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      submitting.current = false;
      setPending(null);
    }
  }

  const settled = complete || decision !== null || expired;
  const contextRows = approvalContextRows(details);
  const riskLabel = details.risk ? riskLabels[details.risk] ?? details.risk : undefined;
  return (
    <section className="approval-card" aria-live="polite">
      <div className="approval-card-main">
        <span className="approval-card-icon" aria-hidden="true">!</span>
        <div className="approval-card-copy">
          <div className="approval-card-kicker">
            <span>{expired && !complete && !decision ? "审批已过期" : settled ? "审批已处理" : approvalLabel("pending")}</span>
            {riskLabel && (
              <span className={`risk-badge risk-${details.risk}`}>
                {riskLabel}风险
              </span>
            )}
          </div>
          <h3>允许执行这个操作？</h3>
          <p>{formatApprovalReason(details.reason)}</p>
        </div>
      </div>
      {error && <p className="domain-error">{error}</p>}
      <div className="domain-actions">
        <button
          className="approve-button"
          type="button"
          disabled={settled || pending !== null}
          onClick={() => void decide("approved")}
        >
          {pending === "approved" ? "正在允许…" : "允许并继续"}
        </button>
        <button
          className="reject-button"
          type="button"
          disabled={settled || pending !== null}
          onClick={() => void decide("rejected")}
        >
          {pending === "rejected" ? "正在拒绝…" : "拒绝"}
        </button>
        {decision && (
          <span className="decision-label">
            {decision === "approved" ? "已允许，运行正在恢复" : "已拒绝"}
          </span>
        )}
      </div>
      <details className="approval-details">
        <summary>
          <span>操作详情</span>
          <span>{details.tool_name || "工具调用"}</span>
        </summary>
        <dl className="domain-metadata">
          {contextRows.map((row) => (
            <div key={row.label}>
              <dt>{row.label}</dt>
              <dd>{row.value}</dd>
            </div>
          ))}
          {contextRows.length === 0 && (
            <div>
              <dt>工具调用</dt>
              <dd>{details.tool_call_id || "未提供"}</dd>
            </div>
          )}
        </dl>
      </details>
    </section>
  );
}
