"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "../auth-provider";
import { StudioSidebar } from "./studio-sidebar";
import { EnvironmentPolicyControlPlane } from "./environment-policy-control-plane";
import { AgentTriggerControlPlane } from "./agent-trigger-control-plane";
import { apiDraftToStudioDraft, studioClient, type StudioCapabilities, type StudioDeployment, type StudioDeploymentSnapshot, type StudioEnvironment, type StudioEvalDataset, type StudioEvalRun } from "../../lib/studio-client";
import type { StudioDraft } from "../../lib/agent-studio";
import { createRandomId } from "../../lib/random-id";
import styles from "./agent-operations-workspace.module.css";

const CASE_STATUS_LABELS: Record<string, string> = {
  passed: "通过",
  failed: "失败",
  error: "错误",
  timed_out: "超时",
  cancelled: "取消",
  queued: "排队中",
  running: "运行中",
  cancelling: "取消中",
};

function caseStatusLabel(status: string): string {
  return CASE_STATUS_LABELS[status] ?? status;
}

const CASE_TAG_LABELS: Record<string, string> = {
  happy: "正常",
  ambiguous: "歧义",
  safety: "安全",
};

export function AgentOperationsWorkspace({ agentName }: { agentName: string }) {
  const { membership } = useAuth();
  const [draft, setDraft] = useState<StudioDraft | null>(null);
  const [capabilities, setCapabilities] = useState<StudioCapabilities | null>(null);
  const [datasets, setDatasets] = useState<StudioEvalDataset[]>([]);
  const [runs, setRuns] = useState<StudioEvalRun[]>([]);
  const [environments, setEnvironments] = useState<StudioEnvironment[]>([]);
  const [deployments, setDeployments] = useState<StudioDeployment[]>([]);
  const [snapshots, setSnapshots] = useState<StudioDeploymentSnapshot[]>([]);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("正在读取运行控制面…");
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [selectedRunId, setSelectedRunId] = useState("");
  const canManage = membership.role === "owner" || membership.role === "admin";

  async function refresh() {
    const [summaries, caps, allDatasets, allRuns, envs, deps, snaps] = await Promise.all([
      studioClient.listAccessibleDrafts(), studioClient.capabilities(), studioClient.listEvalDatasets(), studioClient.listEvalRuns(),
      studioClient.listEnvironments(agentName), studioClient.listDeployments(agentName), studioClient.listDeploymentSnapshots(agentName),
    ]);
    const summary = summaries.find((item) => item.name === agentName);
    if (!summary) throw new Error(`没有找到 Agent：${agentName}`);
    const source = apiDraftToStudioDraft(await studioClient.getDraft(summary.draftId));
    setDraft(source); setCapabilities(caps);
    setDatasets(allDatasets.filter((item) => item.agentName === agentName));
    setRuns(allRuns.filter((item) => item.run.agentName === agentName));
    setEnvironments(envs); setDeployments(deps); setSnapshots(snaps);
    setNotice("Evaluate & Operate 已同步");
  }

  useEffect(() => { void refresh().catch((error) => setNotice(error instanceof Error ? error.message : "加载失败")); }, [agentName]);
  const latestDataset = useMemo(() => [...datasets].sort((a, b) => b.version - a.version)[0], [datasets]);
  const caseTagCounts = useMemo(() => {
    const counts = { happy: 0, ambiguous: 0, safety: 0 };
    for (const item of latestDataset?.cases ?? []) {
      const tag = item.tags.find((candidate) => candidate === "happy" || candidate === "ambiguous" || candidate === "safety") ?? "happy";
      counts[tag] += 1;
    }
    return counts;
  }, [latestDataset]);
  const terminalRuns = useMemo(
    () => runs
      .filter((item) => !["queued", "running", "cancelling"].includes(item.run.status))
      .sort((a, b) => (b.run.completedAt ?? "").localeCompare(a.run.completedAt ?? "")),
    [runs],
  );
  const selectedRun = useMemo(
    () => terminalRuns.find((item) => item.run.evalRunId === selectedRunId) ?? terminalRuns[0] ?? null,
    [terminalRuns, selectedRunId],
  );
  const tagByCaseId = useMemo(() => {
    if (!selectedRun) return new Map<string, string>();
    const dataset = datasets.find(
      (item) => item.datasetId === selectedRun.run.datasetId && item.version === selectedRun.run.datasetVersion,
    );
    const mapping = new Map<string, string>();
    for (const item of dataset?.cases ?? []) {
      const tag = item.tags.find((candidate) => candidate === "happy" || candidate === "ambiguous" || candidate === "safety") ?? "happy";
      mapping.set(item.id, tag);
    }
    return mapping;
  }, [datasets, selectedRun]);
  const caseAnalysis = useMemo(() => {
    if (!selectedRun) return null;
    const counts: Record<string, number> = { passed: 0, failed: 0, error: 0, timed_out: 0, cancelled: 0 };
    let duration = 0;
    const tagStats: Record<string, { passed: number; total: number }> = {};
    const toolCounts = new Map<string, number>();
    for (const item of selectedRun.cases) {
      counts[item.status] += 1;
      duration += item.durationSeconds;
      const tag = tagByCaseId.get(item.caseId) ?? "happy";
      const stat = tagStats[tag] ?? (tagStats[tag] = { passed: 0, total: 0 });
      stat.total += 1;
      if (item.passed) stat.passed += 1;
      for (const tool of item.tools) toolCounts.set(tool, (toolCounts.get(tool) ?? 0) + 1);
    }
    const topTools = [...toolCounts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 4);
    return { counts, duration, tagStats, topTools };
  }, [selectedRun, tagByCaseId]);
  const terminalRunMetrics = useMemo(() => {
    const terminal = runs.filter((item) => !["queued", "running", "cancelling"].includes(item.run.status));
    if (terminal.length === 0) return null;
    const passed = terminal.reduce((total, item) => total + item.passedCases, 0);
    const total = terminal.reduce((sum, item) => sum + item.totalCases, 0);
    return total > 0 ? { runs: terminal.length, passRate: Math.round((passed / total) * 100) } : null;
  }, [runs]);
  const activeRun = runs.find((item) => ["queued", "running", "cancelling"].includes(item.run.status));
  const snapshotById = useMemo(() => new Map(snapshots.map((item) => [item.snapshotId, item])), [snapshots]);

  async function createDataset() {
    if (!draft) return; setBusy("dataset");
    try { await studioClient.createEvalDataset(draft.id, draft.revision, `${draft.displayName} 发布必测集`, latestDataset?.datasetId); await refresh(); }
    catch (error) { setNotice(error instanceof Error ? error.message : "Dataset 创建失败"); }
    finally { setBusy(""); }
  }
  async function importBank(file: File) {
    if (!draft) return; setBusy("import");
    try {
      const extension = file.name.toLowerCase().endsWith(".csv") ? "csv" : "json";
      const baseName = file.name.replace(/\.[^.]+$/, "").trim() || "导入题库";
      const dataset = await studioClient.importEvalDataset(draft.id, draft.revision, baseName, extension as "json" | "csv", await file.text());
      await refresh();
      setNotice(`题库已导入：${dataset.name} v${dataset.version} · ${dataset.cases.length} 用例`);
    }
    catch (error) { setNotice(error instanceof Error ? error.message : "题库导入失败"); }
    finally { setBusy(""); }
  }
  async function runEval() {
    if (!draft?.publishedVersion || !latestDataset) return; setBusy("eval");
    try { await studioClient.createEvalRun(latestDataset, draft.publishedVersion, `operate-eval-${createRandomId()}`); await refresh(); }
    catch (error) { setNotice(error instanceof Error ? error.message : "Eval 启动失败"); }
    finally { setBusy(""); }
  }
  async function promote(environment: StudioEnvironment) {
    if (!draft?.publishedVersion || !draft.publishedPackageHash) return; setBusy(`promote-${environment.name}`);
    try { await studioClient.promoteDeployment(agentName, draft.publishedVersion, environment, draft.publishedPackageHash, draft.executionProfile, environment.name === "canary" && environment.healthySnapshotId ? 10 : 100); await refresh(); }
    catch (error) { setNotice(error instanceof Error ? error.message : "部署失败"); }
    finally { setBusy(""); }
  }

  return <main className={styles.shell}>
    <StudioSidebar active="agents" />
    <section className={styles.content}>
      <header className={styles.hero}><div><span>EVALUATE &amp; OPERATE</span><h1>{draft?.displayName ?? agentName}</h1><p>Dataset、版本评测、环境策略、部署历史与触发器集中在运行控制面。</p></div><Link href={`/studio/agents?draft=${encodeURIComponent(draft?.id ?? "")}&section=evaluation`}>返回 Builder</Link></header>
      <div className={styles.metrics}><article><span>DATASET</span><strong>{latestDataset ? `v${latestDataset.version}` : "未固化"}</strong><small>{latestDataset ? `${latestDataset.cases.length} 用例 · 正常 ${caseTagCounts.happy} / 歧义 ${caseTagCounts.ambiguous} / 安全 ${caseTagCounts.safety}` : "0 cases"}</small></article><article><span>EVAL RUNS</span><strong>{runs.length}</strong><small>{activeRun?.run.status ?? (terminalRunMetrics ? `近 ${terminalRunMetrics.runs} 次通过率 ${terminalRunMetrics.passRate}%` : "无活动运行")}</small></article><article><span>ENVIRONMENTS</span><strong>{environments.length}</strong><small>{deployments.length} 次部署</small></article><article><span>VERSION</span><strong>{draft?.publishedVersion ?? "未发布"}</strong><small>{draft?.runtime ?? "—"}</small></article></div>
      <section className={styles.panel}><header><div><span>01 / EVALUATE</span><h2>耐久 Dataset 与固定版本评测</h2></div><div><button disabled={!canManage || !draft || Boolean(busy)} onClick={() => void createDataset()}>{busy === "dataset" ? "固化中…" : latestDataset ? "创建 Dataset 新版本" : "固化为发布必测集"}</button><button disabled={!canManage || !draft || Boolean(busy) || busy === "import"} onClick={() => fileInputRef.current?.click()}>{busy === "import" ? "导入中…" : "导入题库"}</button><input ref={fileInputRef} hidden type="file" accept=".json,.csv,application/json,text/csv" onChange={(event) => { const file = event.currentTarget.files?.[0]; if (file) void importBank(file); event.currentTarget.value = ""; }} /><button disabled={!canManage || !latestDataset || !draft?.publishedVersion || Boolean(activeRun) || Boolean(busy)} onClick={() => void runEval()}>{busy === "eval" ? "排队中…" : "运行已发布版本 Eval"}</button></div></header>{runs.slice(0, 8).map((item) => <article className={styles.row} key={item.run.evalRunId}><div><strong>{item.run.agentVersion} · Dataset v{item.run.datasetVersion}</strong><small>{item.run.status} · {item.passedCases}/{item.totalCases} 通过</small></div>{["queued", "running", "cancelling"].includes(item.run.status) && <button onClick={() => void studioClient.cancelEvalRun(item.run.evalRunId).then(refresh)}>取消</button>}</article>)}{runs.length === 0 && <p className={styles.empty}>尚无 Eval 运行。</p>}
        {terminalRuns.length > 0 && selectedRun && caseAnalysis && (
          <div className={styles.caseAnalysis}>
            <header>
              <span>用例级结果分析</span>
              <select aria-label="选择 Eval 运行" value={selectedRun.run.evalRunId} onChange={(event) => setSelectedRunId(event.target.value)}>
                {terminalRuns.map((item) => (
                  <option key={item.run.evalRunId} value={item.run.evalRunId}>
                    {`v${item.run.agentVersion} · Dataset v${item.run.datasetVersion} · ${item.run.status in CASE_STATUS_LABELS ? CASE_STATUS_LABELS[item.run.status as keyof typeof CASE_STATUS_LABELS] : item.run.status}`}
                  </option>
                ))}
              </select>
            </header>
            <div className={styles.metricChips}>
              <span data-tone="good">通过 {caseAnalysis.counts.passed}/{selectedRun.totalCases}</span>
              <span data-tone={caseAnalysis.counts.failed ? "bad" : undefined}>失败 {caseAnalysis.counts.failed}</span>
              <span data-tone={caseAnalysis.counts.error ? "bad" : undefined}>错误 {caseAnalysis.counts.error}</span>
              <span data-tone={caseAnalysis.counts.timed_out ? "bad" : undefined}>超时 {caseAnalysis.counts.timed_out}</span>
              {caseAnalysis.counts.cancelled > 0 && <span>取消 {caseAnalysis.counts.cancelled}</span>}
              <span>总时长 {caseAnalysis.duration.toFixed(1)}s</span>
              {Object.entries(caseAnalysis.tagStats).map(([tag, stat]) => (
                <span key={tag}>{CASE_TAG_LABELS[tag] ?? tag}通过 {stat.total > 0 ? Math.round((stat.passed / stat.total) * 100) : 0}%（{stat.passed}/{stat.total}）</span>
              ))}
              {caseAnalysis.topTools.map(([tool, count]) => <span key={tool}>{tool} ×{count}</span>)}
            </div>
            <table className={styles.caseTable}>
              <thead><tr><th>用例</th><th>状态</th><th>时长</th><th>工具</th><th>失败原因</th></tr></thead>
              <tbody>
                {selectedRun.cases.map((item) => (
                  <tr key={item.caseId}>
                    <td>{CASE_TAG_LABELS[tagByCaseId.get(item.caseId) ?? "happy"] ?? "—"} · {item.caseId}</td>
                    <td data-status={item.status}>{CASE_STATUS_LABELS[item.status]}</td>
                    <td>{item.durationSeconds.toFixed(1)}s</td>
                    <td>{item.tools.join(" · ") || "—"}</td>
                    <td>{item.failures.join("；") || (item.approvalRequested ? "该用例触发了审批" : "—")}</td>
                  </tr>
                ))}
                {selectedRun.cases.length === 0 && (
                  <tr><td colSpan={5}>该运行尚未产出用例结果。</td></tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <section className={styles.panel}><header><div><span>02 / DEPLOY</span><h2>环境指针与部署历史</h2></div></header><div className={styles.environmentGrid}>{environments.map((environment) => <article key={environment.name}><span>{environment.name.toUpperCase()}</span><strong>{environment.routes.map((route) => `${snapshotById.get(route.snapshotId)?.agentVersion ?? "unknown"} · ${route.weight}%`).join(" / ") || "尚未部署"}</strong><small>revision {environment.revision}</small><button disabled={!canManage || !draft?.publishedVersion || Boolean(busy)} onClick={() => void promote(environment)}>{busy === `promote-${environment.name}` ? "提交中…" : "部署当前版本"}</button></article>)}</div><div>{deployments.slice(0, 8).map((item) => <article className={styles.row} key={item.deployment.deploymentId}><div><strong>{item.deployment.environment} · {item.deployment.action}</strong><small>{item.target.agentVersion} · {item.deployment.status}{item.deployment.errorCode ? ` · ${item.deployment.errorCode}` : ""}</small></div></article>)}</div></section>
      {capabilities && <EnvironmentPolicyControlPlane agentName={agentName} environments={environments} capabilities={capabilities} canManage={canManage} onUpdated={(updated) => setEnvironments((current) => current.map((item) => item.name === updated.name ? updated : item))} />}
      <AgentTriggerControlPlane agentName={agentName} publishedVersion={draft?.publishedVersion ?? null} environments={environments} canManage={canManage} />
      <footer>{notice}</footer>
    </section>
  </main>;
}
