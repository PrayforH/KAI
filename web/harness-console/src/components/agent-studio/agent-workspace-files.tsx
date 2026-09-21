"use client";
import { useMemo, useState } from "react";
import { WorkbenchRail, type RailFile } from "../workbench-rail";
import { RailFilePreview } from "../rail-file-preview";
import { studioClient, type DeepagentsProjectSource, type ProjectSourceFile } from "../../lib/studio-client";
import type { StudioDraft } from "../../lib/agent-studio";
import { projectSourceChanges } from "../../lib/project-source-changes";
import { ProjectSourceEditor } from "./project-source-editor";
import { ProjectSourceDiff } from "./project-source-diff";
import type { PreviewTurn } from "./agent-preview";
import styles from "./build-workspace.module.css";

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

export function AgentWorkspaceFiles({draft, baseline, turns, onClose}: {
  draft: StudioDraft; baseline: StudioDraft; turns: PreviewTurn[]; onClose: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const source = useMemo(() => agentInternalFiles(draft),[draft]);
  const comparison = useMemo(() => ({before:agentInternalFiles(baseline),after:source}),[baseline,source]);
  const changes = useMemo(() => projectSourceChanges(comparison),[comparison]);
  const files: RailFile[] = [...source.files, ...changes.filter(change => change.status === "deleted").map(change => change.before!)].map(file => {
    const change = changes.find(change => change.path === file.path);
    return {artifact_id:`source:${file.path}`,name:file.path,media_type:"text/plain",size_bytes:file.size,change:change ? ({added:"已新增",modified:"已修改",deleted:"已删除"} as const)[change.status] : undefined};
  });
  const artifacts = [...new Map(turns.flatMap(turn => turn.result.artifacts.filter(file => file.status === "ready")).map(file => [file.artifact_id,file])).values()];
  files.push(...artifacts.map(file => ({artifact_id:file.artifact_id,name:`对话文件/${file.name}`,media_type:file.media_type,downloadHref:studioClient.tryRunArtifactHref(file.artifact_id)})));
  function preview(file: RailFile) {
    if (!file.artifact_id.startsWith("source:")) return <RailFilePreview target={file} />;
    const change = changes.find(change => change.path === file.name);
    const entry = source.files.find(source => source.path === file.name);
    return <div className={styles.fileSource}><header><strong>{file.name}</strong>{file.change && <span>{file.change}</span>}</header>{change ? <ProjectSourceDiff change={change} theme="light" wrap /> : entry?.content != null ? <ProjectSourceEditor path={file.name} content={entry.content} theme="light" wrap /> : <p>{entry?.unavailable}</p>}</div>;
  }
  return <div className={styles.workspaceFiles} data-expanded={expanded}>
    <WorkbenchRail open onClose={onClose} expanded={expanded} onToggleExpanded={() => setExpanded(value => !value)} threadId={draft.id} observabilityHref={null} runPhase={null}
      workspace={{files,loading:false,error:"",renderPreview:preview,note:`内部文件 · 相对本次打开时 r${baseline.revision}${draft.revision !== baseline.revision ? ` → r${draft.revision}` : ""}`}} />
  </div>;
}
