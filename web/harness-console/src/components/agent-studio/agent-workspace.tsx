"use client";
import { AgentOverview } from "./agent-overview";
import { AgentNavigation } from "./agent-navigation";
import agentaStyles from "./agenta-workspace.module.css";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useAuth } from "../auth-provider";
import { studioClient, type StudioDraftSummary, type StudioQualityScore, type StudioEvalDataset, type PersonalAgentVersion } from "../../lib/studio-client";
import { evolutionRequest, type EvolutionJob } from "../../lib/evolution-client";
import { AGENT_SECTIONS, nextAgentAction, qualityGroups, type AgentSection } from "../../lib/agent-workspace";
import { EvolutionWorkspace } from "./evolution-workspace";
import { AgentOperationsWorkspace } from "./agent-operations-workspace";
import styles from "./agent-workspace.module.css";
import { RunTrace } from "./run-trace";
import { AgentIntegrations } from "./agent-integrations";
import { AgentAutomation } from "./agent-automation";

const explanations: Record<AgentSection, string> = {
  overview: "从真实任务出发，构建、验证并持续改进这个智能体。",
  diagnostics: "查看运行质量与反馈，从具体证据发起一次改进。",
  experiments: "围绕一个明确问题提出候选，保留当前基线并运行独立对照。",
  evaluation: "按固定标准审阅逐用例结果，确认改善和回归后给出决定。",
  release: "将已验收版本投入使用，观察实际效果并保留回退路径。",
  datasets: "维护可复用的业务验证标准，保留每次实验使用的数据版本。",
  integrations: "按用途查看当前智能体的工具与集成，区分声明、授权和实际可用状态。",
  automation: "将验证过的能力接入日历计划与外部事件，保持运行可追踪。",
  experience: "保留有来源、有适用条件的经验，经审核后供后续改进参考。",
};
const qualityLabels: Record<string, string> = { run_success: "运行成功", tool_reliability: "工具可靠性", approval_completion: "审批完成", duration_budget: "时长", cost_budget: "费用", artifact_integrity: "产物完整性", user_feedback: "用户反馈", task_success: "任务成功" };
export function AgentWorkspace({ agentName, section, draftId, jobId, evolutionJob, candidateId, objective }: { agentName: string; section: AgentSection; draftId?: string; jobId?: string; evolutionJob?: string; candidateId?: string; objective?: string }) {
  const { user, membership } = useAuth();
  const [draft, setDraft] = useState<StudioDraftSummary | null>(null);
  const [jobs, setJobs] = useState<EvolutionJob[]>([]);
  const [scores, setScores] = useState<StudioQualityScore[]>([]);
  const [datasets, setDatasets] = useState<StudioEvalDataset[]>([]);
  const [versions, setVersions] = useState<PersonalAgentVersion[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [partial, setPartial] = useState<string[]>([]);
  const [problemsOnly, setProblemsOnly] = useState(false);
  const [selectedRun, setSelectedRun] = useState("");
  const reload = useCallback(async () => {
    setError("");
    try {
      const summaries = await studioClient.listAccessibleDrafts();
      const matches = summaries.filter(d => d.name === agentName);
      const source = draftId ? matches.find(d => d.draftId === draftId) : matches.find(d => !d.spaceId) ?? matches[0];
      if (!source) throw new Error("找不到这个智能体，或当前账号没有访问权限。");
      setDraft(source);
      // Personal improvement APIs have no team-space identity. Do not mix scopes by name.
      if (source.spaceId) { setJobs([]); setScores([]); setDatasets([]); setVersions([]); setPartial([]); return; }
      const results = await Promise.allSettled([
        evolutionRequest<EvolutionJob[]>(), studioClient.listQualityScores(agentName), studioClient.listEvalDatasets(),
        source.agentId ? studioClient.listPersonalAgentVersions(source.agentId) : Promise.resolve([] as PersonalAgentVersion[]),
      ]);
      setJobs(results[0].status === "fulfilled" ? results[0].value.filter(j => j.agentName === agentName) : []);
      setScores(results[1].status === "fulfilled" ? results[1].value : []);
      setDatasets(results[2].status === "fulfilled" ? results[2].value.filter(d => d.agentName === agentName) : []);
      setVersions(results[3].status === "fulfilled" ? results[3].value : []);
      setPartial(results.flatMap((r, i) => r.status === "rejected" ? [["改进任务", "运行质量", "评测集", "发布版本"][i]] : []));
    } catch (e) { setError(e instanceof Error ? e.message : "读取失败"); }
    finally { setLoading(false); }
  }, [agentName, draftId]);
  useEffect(() => { void reload(); }, [reload]);
  const base = `/studio/agents/${encodeURIComponent(agentName)}`;
  const href = (target: AgentSection, extra: Record<string, string> = {}) => `${base}?${new URLSearchParams({ section: target, ...(draftId ? { draft: draftId } : {}), ...extra })}`;
  const build = `/studio/agents/${encodeURIComponent(agentName)}?section=playground&draft=${encodeURIComponent(draft?.draftId ?? "")}`;
  const currentVersion = versions.find(v => v.current_version)?.current_version ?? null;
  const play = `/?${new URLSearchParams({ agent: agentName, version: currentVersion ?? "", owner: user.user_id })}`;
  const allRuns = qualityGroups(scores);
  const problem = (items: StudioQualityScore[]) => items.some(s => s.value === null || s.value < 1);
  const visibleRuns = allRuns.filter(([, values]) => !problemsOnly || problem(values));
  const activeRun = visibleRuns.find(([id]) => id === selectedRun) ?? visibleRuns[0];
  const actions = nextAgentAction(jobs);
  const personal = !draft?.spaceId;
  if (loading) return <div className={styles.workspace} aria-busy="true"><div className={styles.skeleton} /><div className={styles.skeleton} /><p role="status">正在加载智能体工作区…</p></div>;
  if (error || !draft) return <div className={styles.workspace}><Link href="/studio/agents">← 智能体</Link><div className={styles.empty} role="alert"><h1>暂时无法打开工作区</h1><p>{error}</p><button onClick={() => void reload()}>重新加载</button></div></div>;
  return <div className={agentaStyles.frame} data-agent-focused="true"><AgentNavigation name={agentName} label={draft.displayName} draftId={draft.draftId} active={section}/><section className={`${styles.workspace} ${agentaStyles.overviewContent}`}>
    <header className={styles.header}><div className={styles.identity}><span className={styles.monogram}>{draft.displayName.slice(0, 1)}</span><div><h1>{draft.displayName}</h1><p>{draft.goal || "为一类工作配置稳定的能力与执行规则。"}</p></div></div><div className={styles.headerActions}><Link href={build}>构建与试运行</Link>{personal && currentVersion ? <Link className={styles.primary} href={play}>开始使用 ↗</Link> : <span className={styles.muted}>{personal ? "尚无可用个人发布版本" : "在团队空间使用"}</span>}</div></header>
    <div className={styles.context}><span>{personal ? "个人工作区" : "团队工作区"}</span><span><i data-published={Boolean(currentVersion)} />{currentVersion ? `个人默认 ${currentVersion}` : personal ? "尚未发布" : "团队管理"}</span><span>构建版本 {draft.version}</span><span>{draft.toolCount ?? 0} 项工具 · {draft.skillCount ?? 0} 项技能</span></div>

    <div className={styles.sectionHeading}><div><h2>{AGENT_SECTIONS.find(([id]) => id === section)?.[1]}</h2><p>{explanations[section]}</p></div><button onClick={() => void reload()} aria-label="刷新工作区">刷新</button></div>
    {!!partial.length && <p className={styles.warning} role="alert">{partial.join("、")}暂时无法读取；此处缺失的数据不代表没有记录。<button onClick={() => void reload()}>重试</button></p>}
    {!personal && section !== "overview" ? <div className={styles.empty}><h3>团队智能体沿用团队管理权限</h3><p>本轮改进实验支持个人智能体。你可以继续配置团队智能体，不会将同名个人智能体的数据混入这里。</p><Link href={build}>进入构建与试运行 →</Link></div> : <>
    {section === "overview" && <><AgentOverview draft={draft} currentVersion={currentVersion} actions={actions} qualityCount={partial.includes("运行质量") ? null : allRuns.length} datasetCount={partial.includes("评测集") ? null : datasets.length} experienceCount={partial.includes("改进任务") ? null : jobs.flatMap(j => j.experiences).filter(e => e.status === "reviewed").length} /><section className={styles.history}><h3>个人发布版本</h3>{versions.length ? <div className={styles.tableScroll}><table><thead><tr><th>版本</th><th>状态</th><th>创建时间</th><th>操作</th></tr></thead><tbody>{versions.map(v => <tr key={v.version}><td><code>{v.version}</code></td><td>{v.version === currentVersion ? "当前默认" : "历史版本"}</td><td>{new Date(v.created_at).toLocaleString()}</td><td><Link href={`/?${new URLSearchParams({ agent: agentName, version: v.version, owner: user.user_id })}`}>使用此版本 ↗</Link></td></tr>)}</tbody></table></div> : <p className={styles.muted}>暂无可读取的个人发布版本。完成构建后可检查发布条件。</p>}</section></>}
    {section === "diagnostics" && <><div className={styles.toolbar}><label><input type="checkbox" checked={problemsOnly} onChange={e => setProblemsOnly(e.target.checked)} />只看异常或证据缺失</label><span>{visibleRuns.length} 条运行 · 已排除评测运行</span></div>{visibleRuns.length ? <div className={styles.diagnostics}><div className={styles.runList}>{visibleRuns.map(([id, values]) => <button key={id} aria-pressed={activeRun?.[0] === id} onClick={() => setSelectedRun(id)}><strong>{problem(values) ? "需要检查" : "已采集质量记录"}</strong><small>{values[0].agentVersion} · {new Date(values[0].createdAt).toLocaleString()}</small><code>{id.slice(0, 20)}…</code></button>)}</div>{activeRun && <article className={styles.runDetail}><h3>质量与反馈</h3><p>先检查原因，再决定修改指令、补充知识或修复工具。</p><dl>{activeRun[1].map(s => <div key={s.scoreId}><dt>{qualityLabels[s.name] ?? s.name}</dt><dd data-tone={s.value === null || s.value < 1 ? "warning" : "good"}>{s.value === null ? "证据缺失" : s.name === "user_feedback" ? `评分 ${s.value}` : s.value === 1 ? "通过" : "需要检查"}</dd></div>)}</dl>{membership.role !== "viewer" && <Link className={styles.primary} href={href("experiments", { objective: `修复运行 ${activeRun[0]} 中的问题：请补充期望行为与验收标准。` })}>基于此运行发起改进 →</Link>}<details><summary>来源记录</summary><code>{activeRun[0]}</code><p>会话：{activeRun[1][0].sessionId}</p></details><RunTrace key={activeRun[0]} runId={activeRun[0]} /></article>}</div> : <div className={styles.empty}><h3>{problemsOnly ? "当前筛选下没有待检查记录" : "等待真实运行证据"}</h3><p>使用已发布版本后，质量记录会显示在这里。未知费用会明确标为证据缺失。</p>{currentVersion && <Link href={play}>开始一次任务 →</Link>}</div>}</>}
    {section === "experiments" && <EvolutionWorkspace agentName={agentName} stage="experiments" initialJob={jobId} objectiveSeed={objective} onChanged={() => void reload()} />}
    {section === "evaluation" && <><EvolutionWorkspace agentName={agentName} stage="review" initialJob={jobId} onChanged={() => void reload()} /><details className={styles.additional}><summary>已发布版本评测与用例分析</summary><AgentOperationsWorkspace agentName={agentName} view="evaluation" /></details></>}
    {section === "release" && <><EvolutionWorkspace agentName={agentName} stage="release" initialJob={jobId ?? evolutionJob} onChanged={() => void reload()} /><h3 className={styles.blockTitle}>环境部署</h3><AgentOperationsWorkspace key={`${evolutionJob ?? "default"}:${candidateId ?? ""}`} agentName={agentName} view="release" evolutionJob={evolutionJob} candidateId={candidateId} /></>}
    {section === "datasets" && <><div className={styles.tableScroll}><table><thead><tr><th>评测集</th><th>版本</th><th>用例数</th><th>发布门禁</th><th>创建时间</th></tr></thead><tbody>{datasets.map(d => <tr key={`${d.datasetId}:${d.version}`}><td><strong>{d.name}</strong><details><summary>查看用例</summary>{d.cases.map(c => <p key={c.id}>{c.id} · {c.prompt}</p>)}</details></td><td>v{d.version}</td><td>{d.cases.length}</td><td>{d.required ? "必测" : "可选"}</td><td>{new Date(d.createdAt).toLocaleString()}</td></tr>)}{!datasets.length && <tr><td colSpan={5}>暂无评测集。先在构建中定义用例，再固化为数据集。</td></tr>}</tbody></table></div><details className={styles.additional}><summary>导入题库或从当前构建创建数据版本</summary><AgentOperationsWorkspace agentName={agentName} view="evaluation" /></details></>}
    {section === "integrations" && <AgentIntegrations draftId={draft.draftId} />}
    {section === "automation" && <AgentAutomation agentName={agentName} version={currentVersion} />}
    {section === "experience" && <EvolutionWorkspace agentName={agentName} stage="experience" initialJob={jobId} onChanged={() => void reload()} />}
    </>}
  </section></div>;
}
