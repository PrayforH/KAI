"use client";

import { useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { studioClient, type DeepagentsProjectComparison, type DeepagentsProjectSource } from "../../lib/studio-client";
import { createFileTreeIconResolver, getBuiltInSpriteSheet } from "@pierre/trees";
import { ProjectFileTree } from "./project-file-tree";
import { projectSourceChanges } from "../../lib/project-source-changes";
import { ProjectSourceDiff } from "./project-source-diff";
import { ProjectSourceEditor } from "./project-source-editor";
import { useCodeTheme } from "./project-code-theme";
import { PanelExpandIcon } from "./builder-panel-icons";
import styles from "./agent-project-code.module.css";

type IconName = "back" | "next" | "search" | "copy" | "download" | "refresh" | "tree" | "code";
function Icon({ name }: { name: IconName }) {
  const paths: Record<IconName, string> = {
    back: "m12 4-6 6 6 6", next: "m8 4 6 6-6 6",
    search: "M13.5 13.5 18 18M15 8.5a6.5 6.5 0 1 1-13 0 6.5 6.5 0 0 1 13 0",
    copy: "M7 6V3h10v11h-3M3 6h11v11H3z",
    download: "M10 2v11m-4-4 4 4 4-4M3 14v4h14v-4",
    refresh: "M16 7A7 7 0 1 0 17 12M16 2v5h-5",
    tree: "M3 3h14v14H3zM12 3v14M5 7h4M5 11h4",
    code: "m6 5-5 5 5 5m8-10 5 5-5 5m-3-12-2 14",
  };
  return <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}

export function sourceFileKind(path: string) {
  if (path.endsWith(".py")) return { mark: "Py", name: "Python", type: "python" };
  if (path.endsWith(".md")) return { mark: "M↓", name: "Markdown", type: "markdown" };
  if (path.endsWith(".json")) return { mark: "{}", name: "JSON", type: "json" };
  if (path.endsWith(".toml")) return { mark: "≡", name: "TOML", type: "config" };
  return { mark: "◇", name: "纯文本", type: "text" };
}
const iconResolver = createFileTreeIconResolver({ set: "complete", colored: true });
// Only trusted, bundled library SVGs enter this map; file names never become markup.
const fileIcons = new Map(Array.from(getBuiltInSpriteSheet("complete").matchAll(/<symbol id="([^"]+)"([^>]*)>([\s\S]*?)<\/symbol>/g), match => [match[1], match[3]]));
function FileMark({ path }: { path: string }) {
  const icon = iconResolver.resolveIcon("file-tree-icon-file", path);
  return <svg className={styles.fileMark} data-type={sourceFileKind(path).type} viewBox="0 0 16 16" aria-hidden="true" dangerouslySetInnerHTML={{ __html: fileIcons.get(icon.name) ?? "" }} />;
}
export function AgentProjectCode({ draftId, revision, name, dirty, onClose, comparison, comparisonPending = false, directoryTarget, expanded = true, onExpandedChange }: {
  draftId: string; revision: number; name: string; dirty: boolean; onClose: () => void; comparison?: DeepagentsProjectComparison; comparisonPending?: boolean;
  directoryTarget?: HTMLElement | null; expanded?: boolean; onExpandedChange?: (expanded: boolean) => void;
}) {
  const theme = useCodeTheme();
  const splitView = directoryTarget !== undefined;
  function selectFile(path: string) { setSelected(path); if (splitView) onExpandedChange?.(true); }
  const [wrap, setWrap] = useState(false);
  const [mode, setMode] = useState<"files" | "changes">(comparison ? "changes" : "files");
  useEffect(() => { if (comparison) setMode("changes"); }, [comparison]);
  const [loadedProject, setProject] = useState<DeepagentsProjectSource | null>(null);
  const [selected, setSelected] = useState("agent.py");
  const [filter, setFilter] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [downloading, setDownloading] = useState(false);
  const [notice, setNotice] = useState("");
  const [treeOpen, setTreeOpen] = useState(true);
  useEffect(() => {
    if (mode === "changes" && comparison) { setLoading(false); setError(""); return; }
    const controller = new AbortController();
    setLoading(true); setError(""); setProject(null); setNotice("");
    if (!draftId) { setLoading(false); setError("先完成智能体创建，即可查看生成的项目代码。"); return; }
    void studioClient.getDeepagentsProjectSource(draftId, revision, controller.signal).then(value => {
      if (controller.signal.aborted) return;
      setProject(value);
      setSelected(current => value.files.some(file => file.path === current) ? current : (value.files[0]?.path ?? ""));
    }).catch(reason => {
      if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "代码加载失败，请重试。");
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [draftId, revision, refresh, mode, comparison]);
  const project = mode === "changes" && comparison ? comparison.after : loadedProject;
  const changes = useMemo(() => comparison ? projectSourceChanges(comparison) : [], [comparison]);
  const files = useMemo(() => mode === "changes" ? changes.map(change => (change.after ?? change.before)!) : project?.files ?? [], [mode, changes, project]);
  useEffect(() => { if (files.length && !files.some(file => file.path === selected)) setSelected(files.find(file => file.path === "agent.py")?.path ?? files[0].path); }, [files, selected]);
  const change = changes.find(item => item.path === selected);
  const visible = useMemo(() => files.filter(file => file.path.toLowerCase().includes(filter.trim().toLowerCase())), [files, filter]);
  const paths = useMemo(() => visible.map(file => file.path), [visible]);
  const file = files.find(item => item.path === selected);
  const index = visible.findIndex(item => item.path === selected);
  const lines = file?.content === undefined || file?.content === null ? 0 : file.content.split("\n").length;
  async function copy() {
    if (file?.content == null) return;
    try { await navigator.clipboard.writeText(file.content); setNotice("文件内容已复制"); }
    catch { setNotice("复制失败，请在代码区选择内容后复制。"); }
  }
  async function download() {
    setDownloading(true); setNotice("");
    try { await studioClient.downloadDeepagentsProject(draftId, project?.revision); setNotice("项目下载已开始"); }
    catch (reason) { setNotice(reason instanceof Error ? reason.message : "下载失败，请重试。"); }
    finally { setDownloading(false); }
  }
  const directory = <aside className={styles.sidebar} aria-label="项目文件">
          <label className={styles.search}><Icon name="search" /><input aria-label="筛选文件" placeholder="筛选文件…" value={filter} onChange={event => setFilter(event.target.value)} />{filter && <button type="button" aria-label="清除筛选" onClick={() => setFilter("")}>×</button>}</label>
          <div className={styles.treeHeading}><span>项目文件</span><small>{visible.length}</small></div>
          <nav className={styles.tree} aria-label="DeepAgents 文件树">{paths.length ? <ProjectFileTree key={mode} paths={paths} selected={splitView && !expanded ? "" : selected} onSelect={selectFile} theme={theme} gitStatus={mode === "changes" ? changes.map(({path, status}) => ({path, status})) : undefined} /> : <p className={styles.noFiles}>没有匹配的文件</p>}</nav>
          <div className={styles.sidebarFooter}><span>DeepAgents {project?.framework_version}</span><button type="button" onClick={() => {setMode("files"); selectFile("README.md");}}>运行说明 ↗</button></div>
        </aside>;
  const panel = <section className={styles.workspace} aria-label="DeepAgents 代码视图" data-tree={!splitView && treeOpen} data-theme={theme}>
    <header className={styles.toolbar}>
      <div className={styles.breadcrumb}><span title={name}>{name}</span><span aria-hidden="true">→</span><strong>DeepAgents</strong><small>{project?.framework_version}</small></div>
      <div className={styles.actions}>
        <button type="button" aria-label="刷新代码" title="刷新代码" disabled={loading || mode === "changes"} onClick={() => setRefresh(value => value + 1)}><Icon name="refresh" /></button>
        <button type="button" aria-label="下载项目" title="下载项目" aria-busy={downloading} disabled={!project || downloading || (mode === "changes" && (comparisonPending || revision !== project.revision))} onClick={() => void download()}><Icon name="download" /></button>
        <button type="button" onClick={splitView ? () => onExpandedChange?.(false) : onClose} className={styles.returnButton}>{splitView ? "收起代码" : "返回配置"}</button>
      </div>
    </header>
    {comparison && <nav className={styles.viewTabs} aria-label="代码内容"><button type="button" aria-pressed={mode === "files"} onClick={() => setMode("files")}>全部文件</button><button type="button" aria-pressed={mode === "changes"} onClick={() => setMode("changes")}>本次改动 · {changes.length}</button></nav>}
    <div className={styles.context}>
      <Icon name="code" /><span>{mode === "changes" && comparison ? `r${comparison.before.revision} → r${comparison.after.revision} · ${comparisonPending ? "待应用" : "已应用"} · ${changes.length} 个文件变化` : dirty ? `有未保存配置 · 当前展示已保存的 r${revision}` : `草稿 r${revision} · 生成的项目代码`}</span><small>只读</small>
      {!splitView && <button type="button" aria-label="显示文件树" aria-pressed={treeOpen} title="显示 / 隐藏文件树" onClick={() => setTreeOpen(value => !value)}><Icon name="tree" /></button>}
    </div>
    {loading ? <div className={styles.loading} role="status" aria-label="正在生成代码"><div /><div /><div /><span>正在生成项目代码…</span></div> : error ?
      <div className={styles.empty} role="alert"><Icon name="code" /><strong>暂时无法展示代码</strong><p>{error}</p><button type="button" onClick={() => setRefresh(value => value + 1)}>重试</button></div> :
      <div className={styles.body}>
        <main className={styles.source}>
          <header className={styles.fileHeader}>
            <FileMark path={selected} /><strong title={selected}>{selected || "选择文件"}</strong>
            {file && mode === "files" && <small>{lines ? `${lines} 行` : `${file.size.toLocaleString()} B`}</small>}
            <button type="button" aria-label="自动换行" title="自动换行" aria-pressed={wrap} onClick={() => setWrap(value => !value)}>↵</button>
            <button type="button" aria-label="复制文件内容" title="复制文件内容" disabled={file?.content == null} onClick={() => void copy()}><Icon name="copy" /></button>
            <button type="button" aria-label="上一个文件" title="上一个文件" disabled={index <= 0} onClick={() => setSelected(visible[index - 1].path)}><Icon name="back" /></button>
            <button type="button" aria-label="下一个文件" title="下一个文件" disabled={!visible.length || index >= visible.length - 1} onClick={() => setSelected(visible[index + 1].path)}><Icon name="next" /></button>
          </header>
          {mode === "changes" && change ? <ProjectSourceDiff change={change} theme={theme} wrap={wrap}/> : file?.content != null ? <ProjectSourceEditor path={file.path} content={file.content} theme={theme} wrap={wrap} /> : <div className={styles.empty}><p>{file?.unavailable ?? "从文件树中选择文件查看源代码。"}</p></div>}
          <footer className={styles.status}><span>{file?.content != null ? `${sourceFileKind(file.path).name} · UTF-8` : "项目资源"}</span><span>只读预览</span><span>{project?.files.length ?? 0} 个文件</span></footer>
        </main>
        {!splitView && treeOpen && directory}
      </div>}
    <div className={styles.notice} role="status" aria-live="polite">{notice}</div>
  </section>;
  return <>
    {splitView && directoryTarget && createPortal(
      <section className={`${styles.workspace} ${styles.directoryWorkspace}`} data-theme={theme} aria-label="代码目录">
        <header className={styles.directoryHeader}>
          <div className={styles.directoryTabs} role="group" aria-label="配置与代码视图">
            <button type="button" aria-pressed="false" onClick={onClose}>配置</button>
            <button type="button" aria-pressed="true">代码</button>
          </div>
          <button type="button" aria-label={expanded ? "收起代码视图" : "展开代码视图"} title={expanded ? "收起代码，返回对话" : "在右侧展开代码"} aria-expanded={expanded} disabled={loading || Boolean(error)} onClick={() => onExpandedChange?.(!expanded)}>
            <PanelExpandIcon expanded={expanded} />
          </button>
        </header>
        <p className={styles.directoryHint}>{dirty ? `有未保存配置 · 展示 r${revision}` : `草稿 r${revision}`} · 点击文件在右侧查看</p>
        {comparison && <nav className={styles.viewTabs} aria-label="目录内容"><button type="button" aria-pressed={mode === "files"} onClick={() => setMode("files")}>全部文件</button><button type="button" aria-pressed={mode === "changes"} onClick={() => setMode("changes")}>本次改动 · {changes.length}</button></nav>}
        {loading ? <div className={styles.loading} role="status"><div/><div/><div/><span>正在读取文件目录…</span></div> : error ? <div className={styles.empty} role="alert"><p>{error}</p><button type="button" onClick={() => setRefresh(value => value + 1)}>重试</button></div> : directory}
      </section>, directoryTarget
    )}
    {(!splitView || expanded) && panel}
  </>;

}
