"use client";
import { useState } from "react";
import type { StudioDraft } from "../../lib/agent-studio";
import { studioClient } from "../../lib/studio-client";
import { PanelResizeHandle } from "../panel-resize-handle";
import type { PreviewTurn } from "./agent-preview";
import styles from "./build-workspace.module.css";
export type BuildChange = { label: string; before: string; after: string };
export function AgentBuildAssets({ draft, turns, changes, pending, onClose, onEdit, initialTab = "config" }: {draft: StudioDraft; turns: PreviewTurn[]; changes: BuildChange[]; pending: boolean; initialTab?: "config" | "changes"; onClose:()=>void; onEdit:(section?: "identity" | "prompt" | "skills" | "capabilities" | "runtime", label?: string)=>void}) {
  const [tab,setTab]=useState<"config"|"files"|"changes">(initialTab);
  const artifacts=turns.flatMap(turn=>turn.result.artifacts.filter(item=>item.status==="ready").map(item=>({...item, revision:turn.result.draftRevision})));
  return <aside className={styles.assets} aria-label="智能体资产"><PanelResizeHandle panel="assets"/>
    <header className={styles.panelHeader}><strong>智能体资产</strong><button type="button" aria-label="收起智能体资产" onClick={onClose}>×</button></header>
    <nav className={styles.assetTabs} aria-label="资产分类">{([["config","配置"],["files","文件"],["changes","改动"]] as const).map(([key,label])=><button key={key} type="button" aria-pressed={tab===key} onClick={()=>setTab(key)}>{label}{key==="changes"&&changes.length>0?` · ${changes.length}`:""}</button>)}</nav>
    <div className={styles.assetContent}>
    {tab==="config"&&<><div className={styles.assetIntro}><strong>{draft.displayName}</strong><small>草稿 r{draft.revision}</small></div><p>{draft.description}</p><button type="button" className={styles.editConfiguration} onClick={() => onEdit()}>编辑完整配置 ↗</button>
      <nav className={styles.configLinks} aria-label="配置编辑入口">{([["prompt", "行为设定"], ["skills", "技能"], ["capabilities", "MCP 与工具"], ["capabilities", "知识库"], ["runtime", "高级配置"]] as const).map(([section, label]) => <button key={label} type="button" onClick={() => onEdit(section, label === "知识库" ? label : undefined)}>{label}<span aria-hidden="true">↗</span></button>)}</nav>
      <details open><summary>提示词与输出要求</summary><pre>{draft.systemPrompt||"尚未配置"}</pre></details>
      <details><summary>Skills · {draft.skills.length}</summary>{draft.skills.map(skill=><details key={skill.name}><summary>{skill.name}</summary><pre>{skill.instructions}</pre></details>)}</details>
      <details><summary>工具 · {draft.builtinTools.length+draft.mcpServers.length}</summary><ul>{[...draft.builtinTools,...draft.mcpServers].map((tool,i)=><li key={i}>{tool}</li>)}</ul></details>
      <details><summary>知识库 · {draft.knowledgeReferences.length}</summary><pre>{JSON.stringify(draft.knowledgeReferences,null,2)}</pre></details></>}
    {tab==="files"&&<><p>测试中生成的交付文件，按对应修订保留。</p>{artifacts.length?artifacts.map(item=><a className={styles.assetFile} key={item.artifact_id} href={studioClient.tryRunArtifactHref(item.artifact_id)} download={item.name}><strong>{item.name}</strong><small>r{item.revision} · {item.media_type}</small></a>):<p className={styles.assetEmpty}>尚无交付文件。完成一次包含文件输出的测试后，会显示在这里。</p>}</>}
    {tab==="changes"&&<><p>{changes.length?(pending?"待应用建议；应用后才会更新草稿。":"最近一次通过构建助手应用的修改。"):"在左侧提出修改要求后，可以在这里查看前后差异。"}</p>{changes.map((change,i)=><details key={i} open><summary>{change.label}</summary><small>修改前</small><pre className={styles.before}>{change.before}</pre><small>修改后</small><pre className={styles.after}>{change.after}</pre></details>)}</>}
    </div>
  </aside>;
}
