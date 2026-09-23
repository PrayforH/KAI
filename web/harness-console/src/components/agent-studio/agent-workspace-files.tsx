"use client";
import { useMemo, useState } from "react";
import { createPortal } from "react-dom";
import type { RailFile } from "../workbench-rail";
import { ProjectFileTree } from "./project-file-tree";
import { PanelExpandIcon, BuilderFilesIcon } from "./builder-panel-icons";
import { useCodeTheme } from "./project-code-theme";
import { RailFilePreview } from "../rail-file-preview";
import { studioClient, type DeepagentsProjectSource, type ProjectSourceFile } from "../../lib/studio-client";
import type { StudioDraft } from "../../lib/agent-studio";
import { projectSourceChanges } from "../../lib/project-source-changes";
import { ProjectSourceEditor } from "./project-source-editor";
import { ProjectSourceDiff } from "./project-source-diff";
import type { PreviewTurn } from "./agent-preview";
import styles from "./agent-project-code.module.css";

export function agentInternalFiles(draft: StudioDraft): DeepagentsProjectSource {
  const file = (path: string, content: string): ProjectSourceFile => ({path,content,size:new TextEncoder().encode(content).length,unavailable:null});
  const files = [file("AGENTS.md", draft.systemPrompt)];
  for (const skill of draft.skills) {
    files.push(file(`skills/${skill.name}/SKILL.md`,skill.instructions));
    for (const item of skill.files ?? []) {
      if (item.path === "SKILL.md") continue;
      files.push({path:`skills/${skill.name}/${item.path}`,content:item.content ?? null,size:item.sizeBytes ?? 0,digest:item.contentSha256 ?? undefined,unavailable:item.content == null ? "二进制或服务端保留文件，可通过智能体导出获取完整内容。" : null});
    }
  }
  return {revision:draft.revision,filename:draft.name,digest:"",framework_version:"",files};
}

export function AgentWorkspaceFiles({draft, baseline, turns, onClose, directoryTarget, expanded = false, onExpandedChange}: {
  draft: StudioDraft; baseline: StudioDraft; turns: PreviewTurn[]; onClose: () => void;
  directoryTarget?: HTMLElement | null; expanded?: boolean; onExpandedChange: (expanded: boolean) => void;
}) {
  const theme = useCodeTheme();
  const [selectedId, setSelectedId] = useState("");
  const [query, setQuery] = useState("");
  const source = useMemo(() => agentInternalFiles(draft),[draft]);
  const comparison = useMemo(() => ({before:agentInternalFiles(baseline),after:source}),[baseline,source]);
  const changes = useMemo(() => projectSourceChanges(comparison),[comparison]);
  const files: RailFile[] = [...source.files, ...changes.filter(change => change.status === "deleted").map(change => change.before!)].map(file => {
    const change = changes.find(change => change.path === file.path);
    return {artifact_id:`source:${file.path}`,name:file.path,media_type:"text/plain",size_bytes:file.size,change:change ? ({added:"已新增",modified:"已修改",deleted:"已删除"} as const)[change.status] : undefined};
  });
  const artifacts = [...new Map(turns.flatMap(turn => turn.result.artifacts.filter(file => file.status === "ready")).map(file => [file.artifact_id,file])).values()];
  files.push(...artifacts.map(file => ({artifact_id:file.artifact_id,name:`对话文件/${file.name}`,media_type:file.media_type,downloadHref:studioClient.tryRunArtifactHref(file.artifact_id)})));
  const selected = files.find(file => file.artifact_id === selectedId) ?? files[0];
  const paths = files.filter(file => file.name.toLowerCase().includes(query.trim().toLowerCase())).map(file => file.name);
  function selectFile(path: string) {
    const file = files.find(file => file.name === path);
    if (file) { setSelectedId(file.artifact_id); onExpandedChange(true); }
  }
  function preview(file: RailFile) {
    if (!file.artifact_id.startsWith("source:")) return <RailFilePreview target={file} />;
    const change = changes.find(change => change.path === file.name);
    const entry = source.files.find(source => source.path === file.name);
    return change ? <ProjectSourceDiff change={change} theme={theme} wrap /> : entry?.content != null ? <ProjectSourceEditor path={file.name} content={entry.content} theme={theme} wrap /> : <div className={styles.empty}><p>{entry?.unavailable}</p></div>;
  }
  const directory = <section className={`${styles.workspace} ${styles.directoryWorkspace}`} data-theme={theme} aria-label="文件目录">
    <header className={styles.directoryHeader}>
      <div className={styles.directoryTabs} role="group" aria-label="配置与文件视图">
        <button type="button" aria-pressed="false" onClick={onClose}>配置</button>
        <button type="button" aria-pressed="true">文件</button>
      </div>
      <button type="button" aria-label={expanded ? "收起文件预览" : "展开文件预览"} title={expanded ? "收起文件，返回对话" : "在右侧展开文件"} aria-expanded={expanded} disabled={!files.length} onClick={() => onExpandedChange(!expanded)}><PanelExpandIcon expanded={expanded} /></button>
    </header>
    <p className={styles.directoryHint}>智能体文件与对话产物 · {files.length} 个文件</p>
    <aside className={styles.sidebar} aria-label="智能体文件列表">
      <label className={styles.search}><BuilderFilesIcon /><input aria-label="搜索智能体文件" placeholder="搜索文件…" value={query} onChange={event => setQuery(event.target.value)} />{query && <button type="button" aria-label="清除文件搜索" onClick={() => setQuery("")}>×</button>}</label>
      <nav className={styles.tree} aria-label="智能体文件树">{paths.length ? <ProjectFileTree paths={paths} selected={expanded ? selected?.name ?? "" : ""} onSelect={selectFile} theme={theme} /> : <p className={styles.noFiles}>没有匹配的文件</p>}</nav>
      <footer className={styles.sidebarFooter}><span>相对 r{baseline.revision}</span><span>{changes.length} 项改动</span></footer>
    </aside>
  </section>;
  return <>
    {directoryTarget ? createPortal(directory, directoryTarget) : directoryTarget === undefined ? directory : null}
    {expanded && <section className={styles.workspace} data-theme={theme} data-tree="false" aria-label="智能体文件预览">
      <header className={styles.toolbar}><div className={styles.breadcrumb}><strong>文件预览</strong></div><div className={styles.actions}>{selected?.downloadHref && <a href={selected.downloadHref} download={selected.name}>下载文件</a>}<button type="button" className={styles.returnButton} onClick={() => onExpandedChange(false)}>收起文件</button></div></header>
      <div className={styles.context}><span>智能体文件与对话产物</span><small>只读</small></div>
      <header className={styles.fileHeader}><BuilderFilesIcon /><strong>{selected?.name ?? "选择文件"}</strong>{selected?.change && <small>{selected.change}</small>}</header>
      <div className={styles.filePreviewBody}>{selected ? preview(selected) : <div className={styles.empty}>选择文件查看内容</div>}</div>
      <footer className={styles.status}><span>只读预览</span><span>{files.length} 个文件</span></footer>
    </section>}
  </>;
}
