"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { assistantRequest, type Objective, type Execution, type Notice } from "../../lib/assistant-client";
import { FeishuConnection } from "./feishu-connection";
import styles from "./assistant-manager.module.css";

type Agent = { name: string; version: string; display_name: string; owner_user_id?: string; space_id?: string };
type Project = { projectId: string; name: string };
const statusLabel = { active: "持续跟进", paused: "已暂停", completed: "已完成", cancelled: "已停止",
  pending: "等待执行", running: "执行中", succeeded: "执行成功", failed: "执行失败" };
const date = (value: string) => new Date(value).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });

export function AssistantManager() {
  const [items, setItems] = useState<Objective[]>([]);
  const [inbox, setInbox] = useState<Notice[]>([]);
  const [selected, setSelected] = useState("");
  const [executions, setExecutions] = useState<Execution[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [tab, setTab] = useState<"objectives" | "inbox" | "feishu">("objectives");
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Objective | null>(null);
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  useEffect(() => {
    const target = new URLSearchParams(window.location.search).get("objective");
    if (target) setSelected(target);
  }, []);
  const selectedItem = items.find(item => item.objectiveId === selected);
  const load = useCallback(async () => {
    const [next, notices] = await Promise.all([
      assistantRequest<Objective[]>("objectives"), assistantRequest<Notice[]>("inbox"),
    ]);
    setItems(next); setInbox(notices);
  }, []);
  useEffect(() => {
    let alive = true;
    void load().catch(e => { if (alive) setError(String(e.message)); }).finally(() => { if (alive) setLoading(false); });
    const timer = setInterval(() => { if (!document.hidden) void load().catch(() => {}); }, 15000);
    return () => { alive = false; clearInterval(timer); };
  }, [load]);
  useEffect(() => {
    if (!selected) { setExecutions([]); return; }
    let alive = true;
    const refresh = async () => {
      const result = await assistantRequest<Execution[]>(`objectives/${selected}/executions`);
      if (alive) setExecutions(result);
    };
    setExecutions([]);
    void refresh().catch(e => setError(e.message));
    const timer = setInterval(() => { if (!document.hidden) void refresh().catch(() => {}); }, 5000);
    return () => { alive = false; clearInterval(timer); };
  }, [selected]);
  useEffect(() => {
    if (!creating) return;
    void Promise.all([fetch("/api/harness/agents"), fetch("/api/studio/projects")]).then(async responses => {
      if (responses.some(r => !r.ok)) throw new Error("无法读取智能体或项目，请刷新后重试。");
      const [catalog, groups] = await Promise.all(responses.map(r => r.json()));
      setAgents((catalog as Agent[]).filter(agent => !agent.space_id)); setProjects(groups);
    }).catch(e => setError(e.message));
  }, [creating]);
  async function perform(action: () => Promise<void>) {
    if (pending.current) return;
    pending.current = true; setBusy(true); setError(""); setMessage("");
    try { await action(); await load(); } catch (e) { setError(e instanceof Error ? e.message : "操作未完成，请重试。"); }
    finally { pending.current = false; setBusy(false); }
  }
  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    const agent = editing ? { name: editing.agentName, version: editing.agentVersion } : agents[Number(form.get("agent"))]; if (!agent) return;
    await perform(async () => {
      const interval = Number(form.get("interval"));
      const item = await assistantRequest<Objective>(editing ? `objectives/${editing.objectiveId}` : "objectives", editing ? "PUT" : "POST", {
        ...(editing ? { expectedRevision: editing.revision } : {}),
        title: form.get("title"), instructions: form.get("instructions"),
        agentName: agent.name, agentVersion: agent.version, projectId: editing ? editing.projectId : form.get("project") || null,
        endsAt: form.get("ends") ? new Date(String(form.get("ends"))).toISOString() : null,
        notificationMode: form.get("notification"),
        completionCriteria: form.get("criteria"),
        schedule: interval ? { intervalMinutes: interval, timezone: "Asia/Shanghai" } : null,
      });
      setSelected(item.objectiveId); setCreating(false); setMessage(editing ? "事项要求已更新，将用于下一次执行。" : "事项已创建，可以立即执行或等待下次检查。"); setEditing(null);
    });
  }
  async function action(kind: "pause" | "resume" | "complete" | "stop") {
    if (!selectedItem) return;
    await perform(async () => {
      await assistantRequest(`objectives/${selectedItem.objectiveId}/actions`, "POST", { action: kind, expectedRevision: selectedItem.revision });
      setMessage(kind === "stop" ? "已停止后续检查，正在执行的任务将收到取消请求。" : "事项状态已更新。");
    });
  }
  return <section className={styles.manager}>
    <header className={styles.header}><div><h1>我的助理</h1><p>把需要持续跟进的工作交给 KAI，随时查看进展、补充要求。</p></div>
      <button className={styles.primary} onClick={() => { setEditing(null); setCreating(true); setTab("objectives"); }}>新建持续事项</button></header>
    <nav className={styles.tabs} aria-label="助理视图">
      {([['objectives', '持续事项'], ['inbox', `待看结果${inbox.some(n => !n.readAt) ? ` · ${inbox.filter(n => !n.readAt).length}` : ''}`], ['feishu', '飞书连接']] as const).map(([id, label]) =>
        <button key={id} aria-pressed={tab === id} onClick={() => setTab(id)}>{label}</button>)}
    </nav>
    {error && <p role="alert" className={styles.error}>{error} <button onClick={() => void perform(async () => {})}>刷新</button></p>}
    {message && <p role="status" className={styles.message}>{message}</p>}
    {tab === "feishu" && <FeishuConnection />}
    {tab === "inbox" && <div className={styles.notices}>{!inbox.length && <p className={styles.empty}>执行结束后，结果会出现在这里。</p>}
      {inbox.map(item => <article key={item.itemId}><div className={styles.row}><h2>{item.title}</h2><time>{date(item.createdAt)}</time></div><p>{item.content}</p><div className={styles.actions}>
        <button onClick={() => { setSelected(item.objectiveId); setTab("objectives"); }}>查看事项</button>
        {!item.readAt && <button disabled={busy} onClick={() => void perform(async () => { await assistantRequest(`inbox/${item.itemId}/read`, "POST"); })}>标为已读</button>}</div></article>)}
    </div>}
    {tab === "objectives" && (creating ? <form className={styles.form} onSubmit={create}>
      <h2>{editing ? "编辑事项" : "新建持续事项"}</h2><label>事项名称<input defaultValue={editing?.title} name="title" required maxLength={120} placeholder="例如：跟进采购整改材料" /></label>
      <label>跟进要求<textarea defaultValue={editing?.instructions} name="instructions" required maxLength={20000} rows={5} placeholder="说明需要做什么、关注哪些变化，以及什么时候需要你确认。" /></label>
      {editing ? <p>执行智能体：{editing.agentName} · {editing.agentVersion}。如需变更智能体或项目，请新建事项。</p> : <div className={styles.columns}><label>执行智能体<select name="agent" required disabled={!agents.length}>{agents.length ? agents.map((a, i) => <option key={`${a.name}:${a.version}`} value={i}>{a.display_name || a.name} · {a.version}</option>) : <option value="">请先发布一个个人智能体</option>}</select></label>
        <label>关联项目<select name="project"><option value="">不关联项目</option>{projects.map(p => <option key={p.projectId} value={p.projectId}>{p.name}</option>)}</select></label></div>}
      <label>检查频率<select name="interval" defaultValue={editing?.schedule?.intervalMinutes ?? 0}><option value="0">仅手动执行</option><option value="60">每小时</option><option value="360">每 6 小时</option><option value="1440">每 24 小时</option></select></label>
      <label>完成标准<input defaultValue={editing?.completionCriteria} name="criteria" maxLength={2000} placeholder="例如：所有整改项已提交证据并确认通过" /></label>
      <label>停止检查时间（可选）<input name="ends" type="datetime-local" defaultValue={editing?.endsAt ? new Date(new Date(editing.endsAt).getTime() - new Date(editing.endsAt).getTimezoneOffset() * 60000).toISOString().slice(0, 16) : ""} /></label>
      <label>飞书通知偏好<select name="notification" defaultValue={editing?.notificationMode ?? "important"}><option value="important">重要变化</option><option value="all_results">每次执行结果</option><option value="none">不推送</option></select></label>
      <p>飞书连接完成后通知偏好才会生效，所有结果仍保留在站内。</p>
      <p>执行使用所选智能体的权限与项目资料。补充要求会在下一次执行时生效。</p><div className={styles.actions}><button type="button" onClick={() => setCreating(false)} disabled={busy}>取消</button><button className={styles.primary} disabled={busy || (!editing && !agents.length)}>{editing ? "保存修改" : "创建事项"}</button></div>
    </form> : <div className={styles.workspace}><div className={styles.list}>
      {loading && <p className={styles.empty}>正在读取事项…</p>}
      {!loading && !items.length && <div className={styles.empty}><h2>从一件需要跟进的工作开始</h2><p>例如材料整改、资料检查或每周研究。写下要求，再选择检查频率。</p></div>}
      {items.map(item => <button className={styles.item} data-selected={selected === item.objectiveId} key={item.objectiveId} onClick={() => setSelected(item.objectiveId)}>
        <span className={styles.row}><strong>{item.title}</strong><span className={styles.badge}>{statusLabel[item.status]}</span></span><span className={styles.excerpt}>{item.instructions}</span>
        <small>{item.nextCheckAt ? `下次检查 ${date(item.nextCheckAt)}` : '无定时检查'}</small></button>)}
    </div><div className={styles.detail}>{selectedItem ? <>
      <div className={styles.row}><h2>{selectedItem.title}</h2><span className={styles.badge}>{statusLabel[selectedItem.status]}</span></div>
      <p className={styles.instructions}>{selectedItem.instructions}</p>{selectedItem.completionCriteria && <p>完成标准：{selectedItem.completionCriteria}</p>}
      <div className={styles.actions}>
        {selectedItem.status === "active" && <><button className={styles.primary} disabled={busy} onClick={() => void perform(async () => { await assistantRequest(`objectives/${selected}/executions`, "POST", undefined, crypto.randomUUID()); setMessage("已安排执行，结果将显示在这里。"); })}>立即执行</button><button disabled={busy} onClick={() => void action("pause")}>暂停</button></>}
        {selectedItem.status === "paused" && <button disabled={busy} onClick={() => void action("resume")}>继续跟进</button>}
        {["active", "paused"].includes(selectedItem.status) && <><button disabled={busy} onClick={() => { setEditing(selectedItem); setCreating(true); }}>编辑要求</button><button disabled={busy} onClick={() => void action("complete")}>标为完成</button><button disabled={busy} onClick={() => void action("stop")}>停止事项</button></>}
      </div>
      {["active", "paused"].includes(selectedItem.status) && <form onSubmit={event => { event.preventDefault(); const form = event.currentTarget; const content = new FormData(form).get("note"); void perform(async () => { await assistantRequest(`objectives/${selected}/notes`, "POST", { content, expectedRevision: selectedItem.revision }); form.reset(); setMessage("补充已保存，将用于下一次执行。"); }); }}><label>补充要求<textarea name="note" rows={3} required maxLength={8000} placeholder="新的资料、限制或需要调整的重点" /></label><button disabled={busy}>保存补充</button></form>}
      <h3>执行记录</h3>{!executions.length && <p className={styles.muted}>尚无执行记录。</p>}{executions.map(run => <article className={styles.execution} key={run.executionId}><div className={styles.row}><strong>{statusLabel[run.status]}</strong><time>{date(run.createdAt)}</time></div>{run.summary && <p>{run.summary}</p>}{run.runId && <Link href={`/?thread=${encodeURIComponent(run.threadId)}`}>打开任务与产物 →</Link>}</article>)}
    </> : <p className={styles.empty}>选择一个事项，查看要求、进展与执行记录。</p>}</div></div>)}
  </section>;
}
