"use client";

import { useEffect, useState } from "react";
import type { StudioSkill } from "../lib/agent-studio";
import { projectClient, type ApiProjectSkill } from "../lib/studio-client";
import { loadTaskModelRoutes, type TaskModelRoute } from "../lib/task-model-catalog";
import { SaveProjectResultButton } from "./save-project-result-button";
import { ProjectModal } from "./project-modal";
import styles from "./project-reuse.module.css";

export function notifyProjectResources(projectId: string) {
  window.dispatchEvent(new CustomEvent("harness:project-results-changed", { detail: { projectId } }));
}

export function ProjectArtifactActions(props: {projectId: string; threadId: string; artifactId: string; sourceTitle?: string}) {
  return <div className={styles.actions}><SaveProjectResultButton projectId={props.projectId} artifactId={props.artifactId} /><ProjectSkillExtractButton {...props} /></div>;
}

export function ProjectSkillExtractButton(props: {projectId: string; threadId: string; artifactId?: string; sourceTitle?: string}) {
  const [open, setOpen] = useState(false);
  return <><button type="button" className={styles.secondary} onClick={() => setOpen(true)}>提炼为项目技能</button>
    {open && <ProjectSkillExtractDialog {...props} onClose={() => setOpen(false)} />}</>;
}

export function ProjectSkillExtractDialog({projectId, threadId, artifactId, sourceTitle, onClose}: {
  projectId: string; threadId: string; artifactId?: string; sourceTitle?: string; onClose: () => void;
}) {
  const [routes, setRoutes] = useState<TaskModelRoute[]>([]);
  const [route, setRoute] = useState("");
  const [goal, setGoal] = useState("");
  const [draft, setDraft] = useState<StudioSkill | null>(null);
  const [name, setName] = useState((sourceTitle ? `${sourceTitle}流程` : "项目工作流程").slice(0,100));
  const [saved, setSaved] = useState<ApiProjectSkill | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { let active = true; loadTaskModelRoutes().then(items => { if(active) setRoutes(items.filter(item=>item.modelType !== "video_generation")); }).catch(()=>{}); return ()=>{ active=false; }; }, []);

  async function generate() {
    setBusy(true); setError("");
    try {
      const reply = await projectClient.extractSkill(projectId, {threadId, artifactId, goal, modelRoute: route || undefined});
      if (reply.status === "ready" && reply.skill) { setDraft(reply.skill); setSaved(null); }
      else setError([reply.reply, ...reply.followUpQuestions].filter(Boolean).join("\n"));
    } catch(cause) { setError(cause instanceof Error ? cause.message : "技能提炼失败"); }
    finally { setBusy(false); }
  }

  async function save(enabled: boolean) {
    if (!draft || !name.trim() || busy) return;
    setBusy(true); setError("");
    try {
      const item = saved ?? await projectClient.saveSkill(projectId, {displayName:name.trim(), skill:draft, sourceThreadId:threadId});
      setSaved(item);
      if (enabled) await projectClient.setSkillEnabled(projectId, item, true);
      notifyProjectResources(projectId); onClose();
    } catch(cause) { setError(cause instanceof Error ? cause.message : "保存项目技能失败"); }
    finally { setBusy(false); }
  }

  return <ProjectModal title="提炼为项目技能" subtitle="保存到当前项目，不修改智能体配置或发布版本。" onClose={onClose}
    footer={<><span className={styles.muted}>{draft ? "项目技能" : ""}</span><div className={styles.actions}>
      <button type="button" className={styles.secondary} onClick={onClose}>取消</button>
      {draft ? <><button type="button" className={styles.secondary} disabled={busy || !name.trim()} onClick={()=>void save(false)}>保存草稿</button><button type="button" className={styles.primary} disabled={busy || !name.trim()} onClick={()=>void save(true)}>{busy ? "保存中…" : "保存并启用"}</button></>
        : <button type="button" className={styles.primary} disabled={busy} onClick={()=>void generate()}>{busy ? "提炼中…" : "生成草稿"}</button>}
    </div></>}>
    {error && <p className={styles.error} role="alert">{error}</p>}
    {!draft ? <div className={styles.fields}>
      <label>需要保留的方法<textarea value={goal} onChange={event=>setGoal(event.target.value)} maxLength={4000} rows={4} placeholder="例如：保留数据清洗和报告生成步骤，将企业和时间范围作为输入。" /></label>
      <label>生成模型<select aria-label="技能生成模型" value={route} onChange={event=>setRoute(event.target.value)} disabled={busy}><option value="">项目可用模型</option>{routes.map(item=><option key={item.id} value={item.id}>{item.label}</option>)}</select></label>
    </div> : <div className={styles.fields}>
      <label>名称<input aria-label="项目技能名称" value={name} maxLength={100} disabled={Boolean(saved)} onChange={event=>setName(event.target.value)} /></label>
      <label>技能标识<input value={draft.name} maxLength={64} disabled={Boolean(saved)} onChange={event=>setDraft({...draft,name:event.target.value})} /></label>
      <label>触发条件与说明<textarea aria-label="项目技能说明" rows={2} maxLength={500} disabled={Boolean(saved)} value={draft.description} onChange={event=>setDraft({...draft,description:event.target.value})} /></label>
      <label>执行步骤<textarea aria-label="项目技能步骤" className={styles.code} rows={10} maxLength={60000} disabled={Boolean(saved)} value={draft.instructions} onChange={event=>setDraft({...draft,instructions:event.target.value})} /></label>
      {draft.files?.map(file=><details key={file.path}><summary>{file.path}</summary><pre className={styles.code}>{file.content ?? "二进制附件"}</pre></details>)}
    </div>}
  </ProjectModal>;
}
