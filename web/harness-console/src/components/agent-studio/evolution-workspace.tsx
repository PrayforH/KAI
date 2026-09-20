"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { studioClient } from "../../lib/studio-client";
import { evolutionRequest, type EvolutionJob } from "../../lib/evolution-client";
import { createRandomId } from "../../lib/random-id";
import { useAuth } from "../auth-provider";
import styles from "./evolution-workspace.module.css";

const labels: Record<string, string> = { active: "进行中", proposed: "待实验", evaluating: "对照评测中", review_pending: "待人工审核", insufficient_evidence: "证据不足", approved: "已批准", releasing: "发布待恢复", released: "已发布", rejected: "已拒绝", cancelled: "已取消", rolled_back: "已回退", budget_exhausted: "预算到期", completed: "已结束", passed: "通过", regression: "发生回归" };
type Source = Awaited<ReturnType<typeof studioClient.getDraft>>;
type Dataset = Awaited<ReturnType<typeof studioClient.listEvalDatasets>>[number] & { split?: string };
export function EvolutionWorkspace({ agentName }: { agentName: string }) {
  const { membership } = useAuth();
  const writable = membership.role !== "viewer";
  const [jobs, setJobs] = useState<EvolutionJob[]>([]);
  const [selected, setSelected] = useState("");
  const [draft, setDraft] = useState<Source | null>(null);
  const [datasets, setDatasets] = useState<Dataset[]>([]);
  const [datasetKey, setDatasetKey] = useState("");
  const [objective, setObjective] = useState("");
  const [target, setTarget] = useState("systemPrompt");
  const [oldText, setOldText] = useState("");
  const [newText, setNewText] = useState("");
  const [reason, setReason] = useState("");
  const [budget, setBudget] = useState(10);
  const [notice, setNotice] = useState("正在读取持续改进任务…");
  const [busy, setBusy] = useState(false);
  const [lesson, setLesson] = useState("");
  const [conditions, setConditions] = useState("");
  const [lessonCandidate, setLessonCandidate] = useState("");
  const job = jobs.find(j => j.jobId === selected) ?? jobs[0];
  async function load() {
    const [all, summaries, bank] = await Promise.all([evolutionRequest<EvolutionJob[]>(), studioClient.listAccessibleDrafts(), studioClient.listEvalDatasets()]);
    setJobs(all.filter(j => j.agentName === agentName));
    const summary = summaries.find(d => d.name === agentName);
    if (summary) setDraft(await studioClient.getDraft(summary.draftId));
    const choices = (bank as Dataset[]).filter(d => d.agentName === agentName && (d.split ?? "validation") === "validation");
    setDatasets(choices);
    setDatasetKey(current => current || (choices[0] ? `${choices[0].datasetId}@${choices[0].version}` : ""));
  }
  useEffect(() => { void load().then(() => setNotice("已同步。请选择固定验证集，并明确这次要修复的问题。" )).catch(e => setNotice(e.message)); }, [agentName]);
  async function act(path: string, payload: object) {
    setBusy(true);
    try {
      const result = await evolutionRequest<EvolutionJob>(path, payload);
      setJobs(current => [result, ...current.filter(j => j.jobId !== result.jobId)]);
      setSelected(result.jobId); setNotice("已保存，版本与证据已同步。");
    } catch (e) { setNotice(e instanceof Error ? e.message : "操作失败"); await load().catch(() => {}); }
    finally { setBusy(false); }
  }
  const prefix = job ? `/${job.jobId}` : "";
  const rev = { expectedRevision: job?.revision };
  return <section className={styles.page}>
    <header className={styles.header}><div><p>持续改进</p><h1>{draft?.spec.displayName ?? agentName}</h1><p>以固定证据提出候选，经对照评测与人工审核后发布。</p></div><Link href={`/studio/agents/${encodeURIComponent(agentName)}/operations`}>评测与环境部署 →</Link></header>
    <p role="status" className={styles.notice}>{notice}</p>
    <section className={styles.panel}><h2>创建改进任务</h2><p>先在 Builder 配置超时、发布基线，再固化验证集。调度按每次执行 $0.10 预留；它不是供应商账单硬上限。实际成本缺失时无法批准。</p>
      <form onSubmit={e => { e.preventDefault(); const ds = datasets.find(d => `${d.datasetId}@${d.version}` === datasetKey); if (draft && ds) void act("", { draftId: draft.draftId, expectedRevision: draft.revision, objective, dataset: { datasetId: ds.datasetId, version: ds.version }, allowedTargets: [target], budget: { maxCostUsd: budget }, idempotencyKey: createRandomId() }); }}>
        <label>改进目标<textarea required minLength={10} value={objective} onChange={e => setObjective(e.target.value)} placeholder="说明失败场景、期望结果与不得退化的行为" /></label>
        <div className={styles.grid}><label>固定验证集<select required value={datasetKey} onChange={e => setDatasetKey(e.target.value)}><option value="">请选择</option>{datasets.map(d => <option key={`${d.datasetId}@${d.version}`} value={`${d.datasetId}@${d.version}`}>{d.name} · v{d.version} · {d.cases.length} 用例</option>)}</select></label>
          <label>允许修改<select value={target} onChange={e => setTarget(e.target.value)}><option value="systemPrompt">业务 Prompt</option>{draft?.spec.skills.map(s => <option key={s.name} value={`skill:${s.name}`}>Skill · {s.name}</option>)}</select></label>
          <label>实验总预留预算（USD）<input type="number" min="0.01" max="100" step="0.01" value={budget} onChange={e => setBudget(Number(e.target.value))} /></label></div>
        <button disabled={busy || !writable || !draft || !datasetKey}>冻结基线并创建任务</button>
      </form>
    </section>
    <div className={styles.layout}><aside className={styles.panel}><h2>改进任务</h2>{jobs.length === 0 && <p>尚无任务。创建后可提交受限修改并运行对照。</p>}{jobs.map(j => <button className={styles.job} aria-pressed={job?.jobId === j.jobId} key={j.jobId} onClick={() => setSelected(j.jobId)}><strong>{j.objective}</strong><span>{labels[j.status] ?? j.status} · 基线 {j.baseline.version}</span></button>)}</aside>
    {job && <div className={styles.details}>
      <section className={styles.panel}><header><h2>{job.objective}</h2><span>{labels[job.status]} · 修订 {job.revision}</span></header><p>基线 {job.baseline.version} · 验证集 v{job.dataset.version} · 预算 ${job.budget.maxCostUsd} · 到期 {new Date(job.expiresAt).toLocaleString()}</p>
        <div className={styles.actions}><button disabled={busy || !writable} onClick={() => void act(`${prefix}/refresh`, rev)}>刷新实验结果</button><button disabled={busy || !writable || job.status !== "active"} onClick={() => void act(`${prefix}/cancel`, rev)}>停止任务</button></div>
      </section>
      {job.status === "active" && <section className={styles.panel}><h2>提交候选修改</h2><form onSubmit={e => { e.preventDefault(); void act(`${prefix}/candidates`, { ...rev, rationale: reason, patches: [{ target: job.allowedTargets[0], oldText, newText, reason }] }); }}>
        <p>目标：{job.allowedTargets[0]}。原文必须在冻结基线中唯一匹配，其他配置保持冻结。</p>
        <label>原文片段<textarea required value={oldText} onChange={e => setOldText(e.target.value)} /></label><label>替换内容<textarea value={newText} onChange={e => setNewText(e.target.value)} /></label><label>修改理由<input required minLength={5} value={reason} onChange={e => setReason(e.target.value)} /></label><button disabled={busy || !writable}>编译并保存候选</button>
      </form></section>}
      {job.candidates.map(c => <section className={styles.panel} key={c.candidateId}>
        <header><h2>{c.spec.version}</h2><span>{labels[c.status] ?? c.status}</span></header><p>{c.rationale}</p><pre className={styles.diff}>{c.diff}</pre>
        {c.comparison && <><div className={styles.metrics}><span>改善 <strong>{c.comparison.improved.length}</strong></span><span>回归 <strong>{c.comparison.regressed.length}</strong></span><span>未解决 <strong>{c.comparison.unresolved.length}</strong></span><span>成本缺失 <strong>{c.comparison.unknownCostCount}</strong></span></div><p>{c.comparison.conclusion}</p><p>基线成本 {c.comparison.baselineCost == null ? "未知" : `$${c.comparison.baselineCost}`} / 候选成本 {c.comparison.candidateCost == null ? "未知" : `$${c.comparison.candidateCost}`}</p><details><summary>对照证据与报告标识</summary><pre>{JSON.stringify(c.comparison, null, 2)}</pre></details></>}
        <details><summary>实验运行（{c.trials.length} 组）</summary>{c.trials.map(t => <p key={t.trialId}>基线：{t.baselineRunId ?? "待调度"}<br />候选：{t.candidateRunId ?? "待调度"}</p>)}</details>
        {c.review && <p>审核：{c.review.reviewer} · {c.review.reason}</p>}
        <div className={styles.actions}>
          {["proposed", "insufficient_evidence"].includes(c.status) && <button disabled={busy || !writable || job.status !== "active"} onClick={() => void act(`${prefix}/candidates/${c.candidateId}/evaluate`, rev)}>运行基线与候选</button>}
          {["review_pending", "insufficient_evidence"].includes(c.status) && <><label>审核意见<input minLength={5} value={reason} onChange={e => setReason(e.target.value)} placeholder="说明证据和批准或拒绝的理由" /></label><button disabled={busy || !writable || reason.length < 5 || c.comparison?.status !== "passed"} onClick={() => void act(`${prefix}/candidates/${c.candidateId}/review`, { ...rev, decision: "approve", reason, reportHash: c.comparison?.reportHash })}>人工批准</button><button disabled={busy || !writable || reason.length < 5} onClick={() => void act(`${prefix}/candidates/${c.candidateId}/review`, { ...rev, decision: "reject", reason, reportHash: c.comparison?.reportHash })}>拒绝候选</button></>}
          {["approved", "releasing"].includes(c.status) && <button disabled={busy || !writable} onClick={() => void act(`${prefix}/candidates/${c.candidateId}/release`, rev)}>发布到个人版本</button>}
          {c.status === "released" && <><button disabled={busy || !writable} onClick={() => void act(`${prefix}/candidates/${c.candidateId}/observe`, rev)}>记录发布后观察</button><Link href={`/studio/agents/${encodeURIComponent(agentName)}/operations?evolutionJob=${encodeURIComponent(job.jobId)}&candidate=${encodeURIComponent(c.candidateId)}`}>环境部署与观察</Link><button disabled={busy || !writable} onClick={() => void act(`${prefix}/candidates/${c.candidateId}/rollback`, rev)}>个人版本回退到基线</button></>}
        </div><p className={styles.small}>个人版本发布会切换新建会话的默认版本。环境灰度与环境回滚由运行控制面管理。</p>
      </section>)}
      {job.candidates.some(c => c.comparison) && <section className={styles.panel}><h2>有来源的经验</h2><p>经验仅在当前所有者范围内保存，经审核后可供后续任务参考，不自动写入个人记忆。</p><form onSubmit={e => { e.preventDefault(); void act(`${prefix}/experiences`, { ...rev, candidateId: lessonCandidate, kind: "evolution_lesson", content: lesson, conditions }); }}><label>来源候选<select required value={lessonCandidate} onChange={e => setLessonCandidate(e.target.value)}><option value="">请选择已评测候选</option>{job.candidates.filter(c => c.comparison).map(c => <option key={c.candidateId} value={c.candidateId}>{c.spec.version}</option>)}</select></label><label>经验内容<textarea required minLength={10} value={lesson} onChange={e => setLesson(e.target.value)} /></label><label>适用条件<input required minLength={5} value={conditions} onChange={e => setConditions(e.target.value)} /></label><button disabled={busy || !writable}>记录经验</button></form>{job.experiences.map(e => <article key={e.experienceId}><p>{e.content}</p><p>{e.conditions} · {e.status} · v{e.version}</p>{e.status !== "deprecated" && <div className={styles.actions}>{e.status === "observed" && <button disabled={busy || !writable} onClick={() => void act(`${prefix}/experiences/${e.experienceId}/reviewed`, rev)}>审核经验</button>}<button disabled={busy || !writable} onClick={() => void act(`${prefix}/experiences/${e.experienceId}/deprecated`, rev)}>撤销经验</button></div>}</article>)}</section>}
      <section className={styles.panel}><h2>发布后观察</h2>{!job.observations?.length && <p>发布后记录真实运行样本，样本不足时保持“未验证”。</p>}{job.observations?.slice().reverse().map((o, i) => <p key={i}>{new Date(o.observedAt).toLocaleString()} · 成功 {o.succeededRuns}/{o.totalRuns} · 成本缺失 {o.unknownCostRuns} · 反馈 {o.feedbackCount}<br />{o.conclusion}</p>)}</section>
      <section className={styles.panel}><h2>活动记录</h2>{job.history.slice().reverse().map((h, i) => <p key={i}>{new Date(h.at).toLocaleString()} · {h.action} · {h.actor}</p>)}</section>
    </div>}</div>
  </section>;
}
