"use client";
import { useEffect, useMemo, useState } from "react";
import { studioClient, type DeepagentsProjectComparison, type DeepagentsProjectSource, type PersonalAgentVersion } from "../../lib/studio-client";
import type { StudioDraft } from "../../lib/agent-studio";
import { projectSourceChanges } from "../../lib/project-source-changes";
import type { BuildChange } from "./agent-build-assets";
import { ProjectSourceDiff } from "./project-source-diff";
import { useCodeTheme } from "./project-code-theme";
import styles from "./agent-studio.module.css";

const emptySource: DeepagentsProjectSource = {revision: 0, filename: "", digest: "", framework_version: "", files: []};
const changeLabels = {added: "新增", deleted: "删除", modified: "修改"};

export function AgentVersionHistory({draft, changes, versions, loading, error, onRetry, canPublish, promoting, confirmVersion, onConfirm, onPromote}: {
  draft: StudioDraft; changes: BuildChange[]; versions: PersonalAgentVersion[]; loading: boolean; error: string;
  onRetry: () => void; canPublish: boolean; promoting: string; confirmVersion: string;
  onConfirm: (version: string) => void; onPromote: (version: string) => void;
}) {
  const theme = useCodeTheme();
  const [selected, setSelected] = useState("draft");
  const [revisions, setRevisions] = useState<{revision: number; updatedAt: string}[]>([]);
  const [revisionError, setRevisionError] = useState("");
  const [revisionLoading, setRevisionLoading] = useState(false);
  const [hasMoreRevisions, setHasMoreRevisions] = useState(false);
  const [revisionRefresh, setRevisionRefresh] = useState(0);
  useEffect(() => {
    if (!draft.id) return;
    const controller = new AbortController();
    setRevisionLoading(true); setRevisionError(""); setRevisions([]);
    void studioClient.listDraftRevisions(draft.id, undefined, controller.signal).then(items => {
      if (!controller.signal.aborted) {setRevisions(items); setHasMoreRevisions(items.length === 50);}
    }).catch(reason => {
      if (!controller.signal.aborted) setRevisionError(reason instanceof Error ? reason.message : "草稿历史读取失败");
    }).finally(() => {if (!controller.signal.aborted) setRevisionLoading(false);});
    return () => controller.abort();
  }, [draft.id, draft.revision, revisionRefresh]);
  async function loadMoreRevisions() {
    setRevisionLoading(true); setRevisionError("");
    try {
      const items = await studioClient.listDraftRevisions(draft.id, revisions.at(-1)?.revision);
      setRevisions(current => [...current, ...items.filter(item => !current.some(old => old.revision === item.revision))]);
      setHasMoreRevisions(items.length === 50);
    } catch (reason) {setRevisionError(reason instanceof Error ? reason.message : "草稿历史读取失败");}
    finally {setRevisionLoading(false);}
  }
  const [baseChoice, setBaseChoice] = useState<string | null>(null);
  const [fileChoice, setFileChoice] = useState("");
  const [retry, setRetry] = useState(0);
  const [result, setResult] = useState<{key: string; comparison?: DeepagentsProjectComparison; error?: string} | null>(null);
  const ordered = useMemo(() => [...versions].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at)), [versions]);
  const version = ordered.find(version => version.version === selected);
  const selectedRevision = selected.startsWith("r:") ? Number(selected.slice(2)) : draft.revision;
  const previousRevision = revisions.find(item => item.revision < selectedRevision);
  const defaultBase = selected === "draft" || selected.startsWith("r:")
    ? (previousRevision ? `r:${previousRevision.revision}` : selected === "draft" ? (ordered.find(item => item.version === draft.publishedVersion)?.version ?? ordered[0]?.version ?? "") : "")
    : (ordered[ordered.findIndex(item => item.version === selected) + 1]?.version ?? "");
  const base = baseChoice ?? defaultBase;
  const draftComparison = selected.startsWith("r:") || base.startsWith("r:") || (selected === "draft" && !base);
  const canCompare = draftComparison ? Boolean(draft.id && revisions.length) : Boolean(draft.agentId && (version || (selected === "draft" && base)));
  const comparisonKey = JSON.stringify([draft.id, draft.revision, draft.agentId, selected, base, draftComparison, retry]);
  useEffect(() => {
    if (!canCompare) return;
    const controller = new AbortController();
    const read = (value: string): Promise<DeepagentsProjectSource> => {
      if (value.startsWith("r:") || (value === "draft" && draftComparison)) return studioClient.getDraftRevisionFiles(draft.id, value === "draft" ? draft.revision : Number(value.slice(2)), controller.signal);
      if (value === "draft") return studioClient.getDraftVersionFiles(draft.id, draft.revision, controller.signal);
      return value ? studioClient.getPersonalAgentVersionFiles(draft.agentId!, value, controller.signal) : Promise.resolve(emptySource);
    };
    void Promise.all([read(base), read(selected)]).then(([before, after]) => {
      if (!controller.signal.aborted) setResult({key: comparisonKey, comparison: {before, after}});
    }).catch(reason => {
      if (!controller.signal.aborted) setResult({key: comparisonKey, error: reason instanceof Error ? reason.message : "版本差异读取失败"});
    });
    return () => controller.abort();
  }, [comparisonKey, canCompare, draft.id, draft.revision, draft.agentId, base, selected, draftComparison]);
  const currentResult = result?.key === comparisonKey ? result : null;
  const differences = useMemo(() => currentResult?.comparison ? projectSourceChanges(currentResult.comparison) : [], [currentResult]);
  const activeChange = differences.find(change => change.path === fileChoice) ?? differences.find(change => change.path === "AGENTS.md") ?? differences[0];
  const label = (value: string) => value === "draft" ? `已保存草稿 r${draft.revision}` : value.startsWith("r:") ? `草稿 r${value.slice(2)}` : value || "初始快照";
  function selectVersion(value: string) { setSelected(value); setBaseChoice(null); setFileChoice(""); onConfirm(""); }

  return <div className={styles.historyLayout}>
    <nav className={styles.historyVersions} aria-label="选择智能体版本">
      <button type="button" aria-current={selected === "draft" ? "true" : undefined} onClick={() => selectVersion("draft")}><strong>草稿 r{draft.revision}</strong><small>{previousRevision ? "与上一修订比较" : "当前配置"}</small></button>
      {revisions.filter(item => item.revision !== draft.revision).map(item => <button type="button" key={`r:${item.revision}`} aria-current={selected === `r:${item.revision}` ? "true" : undefined} onClick={() => selectVersion(`r:${item.revision}`)}><strong>草稿 r{item.revision}</strong><small>{new Date(item.updatedAt).toLocaleString("zh-CN")}</small></button>)}
      {revisionLoading && <p role="status">正在读取草稿历史…</p>}
      {revisionError && <p role="alert">{revisionError}<button type="button" onClick={() => setRevisionRefresh(value => value + 1)}>重试草稿历史</button></p>}
      {hasMoreRevisions && <button type="button" disabled={revisionLoading} onClick={() => void loadMoreRevisions()}>更早的草稿</button>}
      {!!revisions.length && !hasMoreRevisions && <p>草稿记录从 r{revisions.at(-1)?.revision} 开始</p>}
      {!!ordered.length && <p>发布版本</p>}
      {ordered.map(item => <button type="button" key={item.version} aria-current={selected === item.version ? "true" : undefined} onClick={() => selectVersion(item.version)}><strong>{item.version}{item.version === item.current_version && <em>当前</em>}</strong><small>{new Date(item.created_at).toLocaleString("zh-CN")}</small></button>)}
      {loading && <p role="status">正在读取版本…</p>}
      {error && <p role="alert">{error}<button type="button" onClick={onRetry}>重试</button></p>}
      {!loading && !error && !versions.length && <p>尚无发布版本</p>}
    </nav>
    <section className={styles.historyDetail} aria-label="版本修改详情">
      <h3>{label(selected)}</h3>
      {version && <p>发布于 {new Date(version.created_at).toLocaleString("zh-CN")}</p>}
      {canCompare ? <>
        <div className={styles.historyCompareControls}>
          <label>对比基准<select aria-label="对比基准" value={base} onChange={event => {setBaseChoice(event.target.value); setFileChoice("");}}>
            {(selected !== "draft" || !base) && <option value="">初始快照（无更早记录）</option>}
            {selected !== "draft" && <option value="draft">已保存草稿 r{draft.revision}</option>}
            {(selected === "draft" || selected.startsWith("r:")) && revisions.filter(item => item.revision !== selectedRevision).map(item => <option key={`r:${item.revision}`} value={`r:${item.revision}`}>草稿 r{item.revision}</option>)}
            {!selected.startsWith("r:") && ordered.filter(item => item.version !== selected).map(item => <option key={item.version} value={item.version}>{item.version}</option>)}
          </select></label>
          <span>{label(base)} → {label(selected)}</span>
        </div>
        {!currentResult ? <p role="status">正在读取版本快照与差异…</p> : currentResult.error ? <p role="alert">{currentResult.error} <button type="button" onClick={() => setRetry(value => value + 1)}>重试差异</button></p> : differences.length ? <>
          <div className={styles.historyChangeCounts} aria-label="变更统计">{(["added", "modified", "deleted"] as const).map(status => <span key={status} data-status={status}>{changeLabels[status]} {differences.filter(change => change.status === status).length}</span>)}</div>
          <label className={styles.historyFileSelect}>改动文件<select aria-label="查看改动文件" value={activeChange?.path ?? ""} onChange={event => setFileChoice(event.target.value)}>{differences.map(change => <option key={change.path} value={change.path}>{changeLabels[change.status]} · {change.path}</option>)}</select></label>
          {activeChange && <ProjectSourceDiff change={activeChange} theme={theme} wrap />}
        </> : <p>配置与文件内容一致。</p>}
      </> : <><p>{revisionLoading ? "正在读取已保存修订…" : "暂无可读取的快照；下方显示本次打开期间的改动。"}</p>{changes.length ? changes.map((change,index) => <details key={index} open><summary>{change.label === "系统提示词" ? "AGENTS.md · 指令" : change.label}</summary><ProjectSourceDiff theme={theme} wrap change={{path:change.label === "系统提示词" ? "AGENTS.md" : `${change.label}.txt`,status:"modified",before:{path:change.label,content:change.before,size:change.before.length,unavailable:null},after:{path:change.label,content:change.after,size:change.after.length,unavailable:null}}} /></details>) : <p>暂无改动。</p>}</>}
      {version && <div className={styles.historyRestore}>{version.version === version.current_version ? <p>新任务正在使用此版本。</p> : confirmVersion === version.version ? <><p>切换只影响新任务，已有任务保持原版本。</p><button type="button" disabled={Boolean(promoting)} onClick={() => onPromote(version.version)}>{promoting ? "切换中…" : "确认切换"}</button><button type="button" onClick={() => onConfirm("")}>取消</button></> : <button type="button" disabled={!canPublish || Boolean(promoting)} onClick={() => onConfirm(version.version)}>设为当前版本</button>}</div>}
    </section>
  </div>;
}
