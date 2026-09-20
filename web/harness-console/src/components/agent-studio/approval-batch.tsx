"use client";
import { useRef, useState } from "react";
import { studioClient, type StudioTryRun } from "../../lib/studio-client";
import { useAuth } from "../auth-provider";
import styles from "./run-trace.module.css";

type Approval = StudioTryRun["approvals"][number];
export function ApprovalBatch({ approvals }: { approvals: Approval[] }) {
  const { membership } = useAuth();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [results, setResults] = useState<Record<string, { done: boolean; text: string }>>({});
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const writable = membership.role !== "viewer";
  const eligible = (item: Approval) => item.status === "pending" && !results[item.approval_id]?.done && (!item.expires_at || Date.parse(item.expires_at) > Date.now());
  async function decide(decision: "approved" | "rejected") {
    if (lock.current || !writable) return;
    lock.current = true; setBusy(true);
    try {
      for (const item of approvals.filter(a => selected.has(a.approval_id) && eligible(a))) {
        try {
          await studioClient.decideTryRunApproval(item.approval_id, decision);
          setResults(current => ({ ...current, [item.approval_id]: { done: true, text: decision === "approved" ? "已批准本次调用" : "已拒绝本次调用" } }));
          setSelected(current => new Set([...current].filter(id => id !== item.approval_id)));
        } catch (reason) {
          setResults(current => ({ ...current, [item.approval_id]: { done: false, text: reason instanceof Error ? reason.message : "操作失败，请刷新后重试" } }));
        }
      }
    } finally { lock.current = false; setBusy(false); }
  }
  const count = approvals.filter(a => eligible(a) && selected.has(a.approval_id)).length;
  return <section className={styles.trace} aria-label="批量工具审批">
    <header><strong>待处理工具调用</strong><span>已选 {count} 项</span></header>
    <p>核对工具、风险和参数后逐项处理。批准仅对本次调用生效，每项会分别记录结果。</p>
    {approvals.map(item => <div key={item.approval_id}>
      <label><input type="checkbox" aria-label={`选择 ${item.tool_name ?? item.approval_id}`} checked={selected.has(item.approval_id) && eligible(item)} disabled={busy || !writable || !eligible(item)} onChange={event => setSelected(current => { const next = new Set(current); if (event.target.checked) next.add(item.approval_id); else next.delete(item.approval_id); return next; })} /> {item.tool_name ?? "工具调用"} · {item.risk ?? "风险未标注"}</label>
      <details><summary>查看调用参数与原因</summary><p>{item.reason}</p><pre style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>{JSON.stringify(item.argument_summary, null, 2)}</pre>{item.expires_at && <p>有效至 {new Date(item.expires_at).toLocaleString()}</p>}</details>
      {results[item.approval_id] ? <p role="status">{results[item.approval_id].text}</p> : !eligible(item) && <p>已处理或已过期，请以最新运行状态为准。</p>}
    </div>)}
    <div className={styles.filters}><button disabled={busy || !writable || count === 0} onClick={() => void decide("approved")}>{busy ? "逐项处理中…" : `批准所选（${count}）`}</button><button disabled={busy || !writable || count === 0} onClick={() => void decide("rejected")}>拒绝所选</button></div>
  </section>;
}
