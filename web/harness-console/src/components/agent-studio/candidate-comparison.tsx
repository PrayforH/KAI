"use client";
import { useEffect, useState } from "react";
import { studioClient, type StudioEvalRun, type StudioEvalCaseResult } from "../../lib/studio-client";
import { RunTrace } from "./run-trace";
import styles from "./agent-workspace.module.css";

type Trial = { trialId: string; baselineRunId: string | null; candidateRunId: string | null };
type Selection = { id: string; a?: StudioEvalCaseResult; b?: StudioEvalCaseResult };
function change(a?: StudioEvalCaseResult, b?: StudioEvalCaseResult) {
  return !a || !b ? "missing" : a.passed && !b.passed ? "regression" : !a.passed && b.passed ? "improvement" : "same";
}
export function CandidateComparison({ trials }: { trials: Trial[] }) {
  const [pairs, setPairs] = useState<[StudioEvalRun, StudioEvalRun][]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("all");
  const [selection, setSelection] = useState<Selection>();
  const key = JSON.stringify(trials);
  useEffect(() => {
    let active = true; setError(""); setPairs([]); setSelection(undefined); setLoading(true);
    const items: Trial[] = JSON.parse(key);
    void Promise.all(items.filter(t => t.baselineRunId && t.candidateRunId).map(t => Promise.all([
      studioClient.getEvalRun(t.baselineRunId!), studioClient.getEvalRun(t.candidateRunId!),
    ]))).then(result => { if (active) setPairs(result); }).catch(e => { if (active) setError(e.message); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [key]);
  return <div className={styles.comparison}>
    <div className={styles.sectionHeading}><h3>逐用例对照</h3><label>筛选<select value={filter} onChange={e => setFilter(e.target.value)}><option value="all">全部用例</option><option value="regression">发生回归</option><option value="improvement">有所改善</option><option value="missing">证据缺失</option></select></label></div>
    {error ? <p role="alert">对照明细读取失败：{error}</p> : loading ? <p role="status">正在读取两侧评测证据…</p> : !pairs.length ? <p>尚无完整的基线与候选评测对，完成实验后可查看。</p> : pairs.map(([base, candidate], index) => {
      const ids = [...new Set([...base.cases, ...candidate.cases].map(c => c.caseId))];
      const rows = ids.map(id => ({ id, a: base.cases.find(c => c.caseId === id), b: candidate.cases.find(c => c.caseId === id) })).filter(({ a, b }) => filter === "all" || change(a, b) === filter);
      return <div className={styles.tableScroll} key={base.run.evalRunId}><table><caption>第 {index + 1} 组 · 基线 {base.run.agentVersion} / 候选 {candidate.run.agentVersion}</caption><thead><tr><th>用例</th><th>基线</th><th>候选</th><th>变化与诊断</th></tr></thead><tbody>{rows.map(({ id, a, b }) => <tr key={id}><td>{id}</td><td>{a ? a.passed ? "通过" : "未通过" : "缺失"}</td><td>{b ? b.passed ? "通过" : "未通过" : "缺失"}</td><td><span data-tone={change(a,b) === "regression" ? "warning" : change(a,b) === "improvement" ? "good" : "neutral"}>{({ missing: "证据不足", regression: "回归", improvement: "改善", same: "未变化" })[change(a,b)]}</span> <button onClick={() => setSelection({ id, a, b })}>查看证据</button></td></tr>)}{!rows.length && <tr><td colSpan={4}>当前筛选下没有用例。</td></tr>}</tbody></table></div>;
    })}
    {selection && <section aria-label={`用例 ${selection.id} 对照证据`}><div className={styles.sectionHeading}><h3>{selection.id} · 执行证据</h3><button onClick={() => setSelection(undefined)}>收起证据</button></div><div className={styles.evidencePair}>{[["基线", selection.a], ["候选", selection.b]].map(([label, item]) => {
      const result = item as StudioEvalCaseResult | undefined;
      return <article key={String(label)}><h3>{String(label)}</h3>{result ? <><p>{result.status} · {result.durationSeconds.toFixed(2)} 秒 · 工具：{result.tools.join("、") || "无"}</p><p>{result.failures.join("；") || "无失败断言"}</p>{result.runId ? <RunTrace runId={result.runId} /> : <p>运行标识缺失，无法读取 Trace。</p>}</> : <p>该侧没有用例结果。</p>}</article>;
    })}</div></section>}
  </div>;
}
