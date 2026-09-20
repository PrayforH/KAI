"use client";
import { useEffect, useState } from "react";
import { studioClient, type StudioEvalRun } from "../../lib/studio-client";
import styles from "./agent-workspace.module.css";

type Trial = { trialId: string; baselineRunId: string | null; candidateRunId: string | null };
export function CandidateComparison({ trials }: { trials: Trial[] }) {
  const [pairs, setPairs] = useState<[StudioEvalRun, StudioEvalRun][]>([]);
  const [error, setError] = useState("");
  const [onlyChanged, setOnlyChanged] = useState(false);
  const key = JSON.stringify(trials);
  useEffect(() => {
    let active = true; setError(""); setPairs([]);
    const items: Trial[] = JSON.parse(key);
    void Promise.all(items.filter(t => t.baselineRunId && t.candidateRunId).map(t => Promise.all([
      studioClient.getEvalRun(t.baselineRunId!), studioClient.getEvalRun(t.candidateRunId!),
    ]))).then(result => { if (active) setPairs(result); }).catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, [key]);
  return <div className={styles.comparison}>
    <div className={styles.sectionHeading}><h3>逐用例对照</h3><label><input type="checkbox" checked={onlyChanged} onChange={e => setOnlyChanged(e.target.checked)} />只看结果变化</label></div>
    {error ? <p role="alert">对照明细读取失败：{error}</p> : !pairs.length ? <p role="status">正在读取两侧评测证据…</p> : pairs.map(([base, candidate], index) => {
      const ids = [...new Set([...base.cases, ...candidate.cases].map(c => c.caseId))];
      const rows = ids.map(id => ({ id, a: base.cases.find(c => c.caseId === id), b: candidate.cases.find(c => c.caseId === id) })).filter(({ a, b }) => !onlyChanged || a?.status !== b?.status);
      return <div className={styles.tableScroll} key={base.run.evalRunId}><table><caption>第 {index + 1} 组 · 基线与候选使用同一验证集</caption><thead><tr><th>用例</th><th>基线</th><th>候选</th><th>变化与诊断</th></tr></thead><tbody>{rows.map(({ id, a, b }) => <tr key={id}><td>{id}</td><td>{a ? a.passed ? "通过" : "未通过" : "缺失"}</td><td>{b ? b.passed ? "通过" : "未通过" : "缺失"}</td><td><span data-tone={a?.passed && !b?.passed ? "warning" : !a?.passed && b?.passed ? "good" : "neutral"}>{!a || !b ? "证据不足" : a.passed && !b.passed ? "回归" : !a.passed && b.passed ? "改善" : "未变化"}</span>{(b?.failures.length || a?.failures.length) ? <details><summary>失败原因</summary><p>基线：{a?.failures.join("；") || "无"}</p><p>候选：{b?.failures.join("；") || "无"}</p></details> : null}</td></tr>)}{!rows.length && <tr><td colSpan={4}>没有结果变化的用例。</td></tr>}</tbody></table></div>;
    })}
  </div>;
}
