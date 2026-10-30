"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { assistantRequest, type Objective, type Execution, type Notice, type Specialist, type Handover, type ManagedPanel } from "../../lib/assistant-client";
import styles from "./assistant-manager.module.css";

type Agent = { name: string; version: string; display_name: string; owner_user_id?: string; space_id?: string };
type Project = { projectId: string; name: string };
const statusLabel = { active: "持续跟进", paused: "已暂停", completed: "已完成", cancelled: "已停止",
  pending: "等待执行", running: "执行中", succeeded: "执行成功", failed: "执行失败" };
const specialistLabel = { draft: "待配置", needs_input: "待补充配置", validating: "验证中",
  ready: "待启用", active: "运行中", paused: "已暂停", blocked: "受阻", archived: "已归档" };
const handoverLabel = { sent: "已发送", received: "已接收", applied: "已安排执行",
  executing: "执行中", pending_input: "待补充", failed: "失败" };
// 运行中的子助理如果承接的职责都没有定时，实际只能手动执行：如实标注。
const specialistState = (specialist: Specialist, objectives: Objective[]) => {
  const base = specialistLabel[specialist.status];
  if (specialist.status !== "active") return base;
  const duties = objectives.filter((item) => item.specialistId === specialist.specialistId);
  const scheduled = duties.some((item) => Boolean(item.schedule));
  return scheduled ? base : "已启用（职责未设定时）";
};
const budgetFromForm = (form: FormData): Record<string, unknown> | null => {
  const number = (key: string) => {
    const raw = String(form.get(key) ?? "").trim();
    if (!raw) return undefined;
    const value = Number(raw);
    return Number.isFinite(value) ? value : undefined;
  };
  const budget: Record<string, unknown> = {};
  for (const key of ["maxRunsPerDay", "maxTotalRuns", "maxTokens", "maxNewTasks"]) {
    const value = number(key);
    if (value !== undefined) budget[key] = value;
  }
  if (form.get("skipUnchanged")) budget.skipUnchanged = true;
  if (form.get("requiresDesktop")) budget.requiresDesktop = true;
  return Object.keys(budget).length ? budget : null;
};
const describeBudget = (budget: Record<string, unknown>) => {
  const labels: [string, string][] = [
    ["maxRunsPerDay", "每日运行"], ["maxTotalRuns", "总运行"], ["maxTokens", "token"],
    ["maxCostUsd", "费用上限"], ["maxNewTasks", "可新建任务"], ["skipUnchanged", "无变化跳过"],
    ["requiresDesktop", "需要共享桌面"],
  ];
  const parts = labels
    .filter(([key]) => budget[key] !== undefined && budget[key] !== null && budget[key] !== false)
    .map(([key, label]) => `${label} ${String(budget[key])}`);
  return parts.length ? parts.join(" · ") : "未设置上限";
};
const date = (value: string) => new Date(value).toLocaleString("zh-CN", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });

export function AssistantManager() {
  const [items, setItems] = useState<Objective[]>([]);
  const [inbox, setInbox] = useState<Notice[]>([]);
  const [selected, setSelected] = useState("");
  const [executions, setExecutions] = useState<Execution[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [tab, setTab] = useState<"objectives" | "inbox" | "specialists" | "managed">("objectives");
  const [panel, setPanel] = useState<ManagedPanel | null>(null);
  const [bindThread, setBindThread] = useState("");
  const [panelRevision, setPanelRevision] = useState(0);
  const [specialists, setSpecialists] = useState<Specialist[]>([]);
  const [handovers, setHandovers] = useState<Handover[]>([]);
  const [selectedSpecialist, setSelectedSpecialist] = useState("");
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
  useEffect(() => {
    if (tab !== "managed" || !selected) { setPanel(null); return; }
    let active = true;
    void assistantRequest<ManagedPanel>(`objectives/${selected}/managed`)
      .then((data) => { if (active) setPanel(data); })
      .catch(() => { if (active) setPanel(null); });
    return () => { active = false; };
  }, [selected, tab, panelRevision]);
  const selectedSpecialistItem = specialists.find(item => item.specialistId === selectedSpecialist);
  const load = useCallback(async () => {
    const [next, notices] = await Promise.all([
      assistantRequest<Objective[]>("objectives"), assistantRequest<Notice[]>("inbox"),
    ]);
    setItems(next); setInbox(notices);
    void assistantRequest<Specialist[]>("specialists").then(list => setSpecialists(list)).catch(() => {});
    void assistantRequest<Handover[]>("handovers").then(list => setHandovers(list)).catch(() => {});
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
        budget: budgetFromForm(form),
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
    <header className={styles.header}><div><h1>持续事项</h1><p>把需要持续跟进的工作交给 KAI，随时查看进展、补充要求。</p></div>
      <button className={styles.primary} onClick={() => { setEditing(null); setCreating(true); setTab("objectives"); }}>新建持续事项</button></header>
    <nav className={styles.tabs} aria-label="助理视图">
      {([['objectives', '持续事项'], ['inbox', `待看结果${inbox.some(n => !n.readAt) ? ` · ${inbox.filter(n => !n.readAt).length}` : ''}`], ['specialists', '专职子助理'], ['managed', '项目托管']] as const).map(([id, label]) =>
        <button key={id} aria-pressed={tab === id} onClick={() => setTab(id)}>{label}</button>)}
    </nav>
    {error && <p role="alert" className={styles.error}>{error} <button onClick={() => void perform(async () => {})}>刷新</button></p>}
    {message && <p role="status" className={styles.message}>{message}</p>}
    {tab === "inbox" && <div className={styles.notices}>{!inbox.length && <p className={styles.empty}>执行结束后，结果会出现在这里。</p>}
      {inbox.map(item => <article key={item.itemId}><div className={styles.row}><h2>{item.title}</h2><time>{date(item.createdAt)}</time></div><p>{item.content}</p><div className={styles.actions}>
        <button onClick={() => { setSelected(item.objectiveId); setTab("objectives"); }}>查看事项</button>
        {item.threadId && <Link href={`/?thread=${encodeURIComponent(item.threadId)}`}>打开原任务 →</Link>}
        {!item.readAt && <button disabled={busy} onClick={() => void perform(async () => { await assistantRequest(`inbox/${item.itemId}/read`, "POST"); })}>标为已读</button>}</div></article>)}
    </div>}
    {tab === "managed" && <div className={styles.workspace}><div className={styles.list}>
      {!items.length && <div className={styles.empty}><h2>还没有可托管的职责</h2><p>先在「持续事项」里创建职责，或在对话里让 KAI 接手一个项目。</p></div>}
      {items.map(item => <button className={styles.item} data-selected={selected === item.objectiveId} key={item.objectiveId} onClick={() => setSelected(item.objectiveId)}>
        <span className={styles.row}><strong>{item.title}</strong><span className={styles.badge}>{statusLabel[item.status]}</span></span>
        <span className={styles.excerpt}>{item.instructions}</span>
      </button>)}
    </div><div className={styles.detail}>{selectedItem ? <>
      <div className={styles.row}><h2>{selectedItem.title}</h2><span className={styles.badge}>{statusLabel[selectedItem.status]}</span></div>
      <p className={styles.muted}>职责 {selectedItem.objectiveId} · revision {selectedItem.revision}</p>
      {panel?.budget && <p className={styles.muted}>预算与授权：{describeBudget(panel.budget)}</p>}
      <h3>纳管任务（{panel?.managed.length ?? 0}）</h3>
      {!panel?.managed.length && <p className={styles.muted}>尚未纳管任务。可在下方按线程 ID 加入，或在对话里点名任务由 KAI 接管。</p>}
      {panel?.managed.map(task => <article className={styles.execution} key={task.threadId}>
        <div className={styles.row}>
          <strong>{task.progress?.title || task.threadId}</strong>
          <span className={styles.badge}>{task.role === "execute" ? "执行托管" : "只读关注"}</span>
        </div>
        <p className={styles.muted}>
          {task.progress ? `${task.progress.active ? "进行中" : task.progress.status}：${task.progress.summary || "暂无记录"}` : "读不到该线程的最近运行"}
          {task.dependsOnObjectiveId ? ` · 依赖 ${task.dependsOnObjectiveId}` : ""}
        </p>
        {task.progress?.metrics || task.progress?.duration_seconds != null ? (
          <p className={styles.muted}>
            {[
              task.progress.metrics
                ? task.progress.metrics.unknownUsage || task.progress.metrics.tokens == null
                  ? "tokens 未计量"
                  : `tokens ${task.progress.metrics.tokens.toLocaleString("zh-CN")}`
                : null,
              task.progress.metrics?.costUsd != null ? `cost $${task.progress.metrics.costUsd}` : null,
              task.progress.duration_seconds != null
                ? `耗时 ${Math.max(1, Math.round(task.progress.duration_seconds))} 秒`
                : null,
            ].filter(Boolean).join(" · ")}
          </p>
        ) : null}
        <Link href={`/?thread=${encodeURIComponent(task.threadId)}`}>打开原任务 →</Link>
      </article>)}
      {panel?.aggregate && <pre className={styles.aggregate}>{panel.aggregate}</pre>}
      <form onSubmit={event => { event.preventDefault();
        void perform(async () => {
          await assistantRequest(`objectives/${selectedItem.objectiveId}/managed`, "POST", { threadId: bindThread.trim() });
          setBindThread("");
          setPanelRevision((current) => current + 1);
          setMessage("已纳入该职责的关注范围（只读跟进）。");
        });
      }}><label>纳管一个既有任务（线程 ID）<input value={bindThread} onChange={e => setBindThread(e.target.value)} required maxLength={128} placeholder="例如 kai-personal-assistant-v1 之外的线程 ID" /></label>
      <button disabled={busy || !bindThread.trim()}>纳管</button></form>
    </> : <p className={styles.empty}>选择一个职责，查看它的纳管任务、成果汇总与预算授权。</p>}</div></div>}
    {tab === "specialists" && <div className={styles.workspace}><div className={styles.list}>
      {!specialists.length && <div className={styles.empty}><h2>还没有专职子助理</h2><p>在对话里说“帮我长期关注某账号花费”，KAI 会按职责创建专职子助理并给出转交回执；缺配置时不会启动。</p></div>}
      {specialists.map(item => <button className={styles.item} data-selected={selectedSpecialist === item.specialistId} key={item.specialistId} onClick={() => setSelectedSpecialist(item.specialistId)}>
        <span className={styles.row}><strong>{item.name}</strong><span className={styles.badge}>{specialistState(item, items)}</span></span>
        <span className={styles.excerpt}>{item.description || item.agentName}</span>
        <small>{handovers.find(h => h.specialistId === item.specialistId) ? `最近转交：${handoverLabel[handovers.find(h => h.specialistId === item.specialistId)!.status]}` : "尚无转交"}</small></button>)}
    </div><div className={styles.detail}>{selectedSpecialistItem ? <>
      <div className={styles.row}><h2>{selectedSpecialistItem.name}</h2><span className={styles.badge}>{specialistState(selectedSpecialistItem, items)}</span></div>
      <p className={styles.instructions}>{selectedSpecialistItem.description || "（未填写职责说明）"}</p>
      <p className={styles.muted}>执行智能体：{selectedSpecialistItem.agentName} · {selectedSpecialistItem.agentVersion}</p>
      <div className={styles.actions}>
        {selectedSpecialistItem.status === "ready" && <button className={styles.primary} disabled={busy} onClick={() => void perform(async () => {
          await assistantRequest(`specialists/${selectedSpecialistItem.specialistId}/status`, "POST", { status: "active", expectedRevision: selectedSpecialistItem.revision });
          setMessage("已启用持续运行；下一次检查时间以服务端记录为准。");
        })}>启用持续运行</button>}
        {selectedSpecialistItem.status === "active" && <button disabled={busy} onClick={() => void perform(async () => {
          await assistantRequest(`specialists/${selectedSpecialistItem.specialistId}/status`, "POST", { status: "paused", expectedRevision: selectedSpecialistItem.revision });
          setMessage("已暂停该子助理的全部职责。");
        })}>暂停</button>}
      </div>
      <form onSubmit={event => { event.preventDefault(); const form = event.currentTarget;
        const schedule = Number(new FormData(form).get("schedule")); const notify = String(new FormData(form).get("notify"));
        void perform(async () => { await assistantRequest(`specialists/${selectedSpecialistItem.specialistId}/config`, "POST",
          { config: { ...(schedule ? { schedule_minutes: schedule } : {}), ...(notify ? { notify_channel: notify } : {}) }, expectedRevision: selectedSpecialistItem.revision });
          setMessage("配置已保存；缺项补齐后状态会转为待启用。"); });
      }}><label>检查频率<select name="schedule" defaultValue={Number(selectedSpecialistItem.config.schedule_minutes ?? 0)}><option value="0">（不修改）</option><option value="60">每小时</option><option value="360">每 6 小时</option><option value="1440">每 24 小时</option></select></label>
      <label>通知渠道<select name="notify" defaultValue={String(selectedSpecialistItem.config.notify_channel ?? "")}><option value="">（不修改）</option><option value="inbox">站内收件</option></select></label>
      <button disabled={busy}>保存配置</button></form>
      <form onSubmit={event => { event.preventDefault(); const form = event.currentTarget;
        const objectiveId = String(new FormData(form).get("duty") || "");
        if (!objectiveId || !selectedSpecialistItem) return;
        void perform(async () => {
          await assistantRequest(`specialists/${selectedSpecialistItem.specialistId}/duties`, "POST",
            { objectiveId, expectedRevision: selectedSpecialistItem.revision });
          form.reset();
          setMessage("已把该职责挂到子助理；之后可试运行并启用持续运行。");
        });
      }}><label>把一项既有职责挂到这个子助理<select name="duty" defaultValue="" required>
        <option value="">选择职责…</option>
        {items.filter(item => !item.specialistId).map(item => (
          <option key={item.objectiveId} value={item.objectiveId}>{item.title}</option>
        ))}
      </select></label><button disabled={busy}>挂载职责</button></form>
      {["ready", "active"].includes(selectedSpecialistItem.status) && <div className={styles.actions}>
        <button disabled={busy} onClick={() => void perform(async () => {
          const trial = await assistantRequest<{ executionId: string; scheduled: string }>(
            `specialists/${selectedSpecialistItem.specialistId}/trial`, "POST",
            { expectedRevision: selectedSpecialistItem.revision },
          );
          setMessage(`${trial.scheduled}（执行 ${trial.executionId}）`);
        })}>首次试运行</button>
      </div>}
      <h3>转交记录</h3>
      {!handovers.filter(h => h.specialistId === selectedSpecialistItem.specialistId).length && <p className={styles.muted}>尚无转交记录。</p>}
      {handovers.filter(h => h.specialistId === selectedSpecialistItem.specialistId).map(h => <article className={styles.execution} key={h.handoverId}>
        <div className={styles.row}><strong>{handoverLabel[h.status]}</strong><time>{date(h.createdAt)}</time></div>
        {h.summary && <p>{h.summary}</p>}
        {h.objectiveId && <Link href={`/?thread=${encodeURIComponent(`assistant_${h.objectiveId}`)}`}>打开职责会话 →</Link>}
      </article>)}
    </> : <p className={styles.empty}>选择一个专职子助理，查看配置与转交记录。</p>}</div></div>}
    {tab === "objectives" && (creating ? <form className={styles.form} onSubmit={create}>
      <h2>{editing ? "编辑事项" : "新建持续事项"}</h2><label>事项名称<input defaultValue={editing?.title} name="title" required maxLength={120} placeholder="例如：跟进采购整改材料" /></label>
      <label>跟进要求<textarea defaultValue={editing?.instructions} name="instructions" required maxLength={20000} rows={5} placeholder="说明需要做什么、关注哪些变化，以及什么时候需要你确认。" /></label>
      {editing ? <p>执行智能体：{editing.agentName} · {editing.agentVersion}。如需变更智能体或项目，请新建事项。</p> : <div className={styles.columns}><label>执行智能体<select name="agent" required disabled={!agents.length}>{agents.length ? agents.map((a, i) => <option key={`${a.name}:${a.version}`} value={i}>{a.display_name || a.name} · {a.version}</option>) : <option value="">请先发布一个个人智能体</option>}</select></label>
        <label>关联项目<select name="project"><option value="">不关联项目</option>{projects.map(p => <option key={p.projectId} value={p.projectId}>{p.name}</option>)}</select></label></div>}
      <label>检查频率<select name="interval" defaultValue={editing?.schedule?.intervalMinutes ?? 0}><option value="0">仅手动执行</option><option value="60">每小时</option><option value="360">每 6 小时</option><option value="1440">每 24 小时</option></select></label>
      <label>完成标准<input defaultValue={editing?.completionCriteria} name="criteria" maxLength={2000} placeholder="例如：所有整改项已提交证据并确认通过" /></label>
      <label>停止检查时间（可选）<input name="ends" type="datetime-local" defaultValue={editing?.endsAt ? new Date(new Date(editing.endsAt).getTime() - new Date(editing.endsAt).getTimezoneOffset() * 60000).toISOString().slice(0, 16) : ""} /></label>
      <label>飞书通知偏好<select name="notification" defaultValue={editing?.notificationMode ?? "important"}><option value="important">重要变化</option><option value="all_results">每次执行结果</option><option value="none">不推送</option></select></label>
      <fieldset className={styles.budgetFieldset}>
        <legend>预算与授权范围（可选）</legend>
        <div className={styles.columns}>
          <label>每日运行上限<input name="maxRunsPerDay" type="number" min={1} max={10000} defaultValue={editing ? String(editing.budget?.maxRunsPerDay ?? "") : ""} placeholder="不限" /></label>
          <label>总运行上限<input name="maxTotalRuns" type="number" min={1} max={100000} defaultValue={editing ? String(editing.budget?.maxTotalRuns ?? "") : ""} placeholder="不限" /></label>
        </div>
        <div className={styles.columns}>
          <label>token 上限<input name="maxTokens" type="number" min={1} defaultValue={editing ? String(editing.budget?.maxTokens ?? "") : ""} placeholder="需平台计量可用" /></label>
          <label>可新建任务数<input name="maxNewTasks" type="number" min={0} max={100} defaultValue={editing ? String(editing.budget?.maxNewTasks ?? "") : ""} placeholder="留空＝无权新建" /></label>
        </div>
        <label className={styles.checkbox}><input name="skipUnchanged" type="checkbox" defaultChecked={Boolean(editing?.budget?.skipUnchanged)} />无变化时跳过定时检查（不唤起模型）</label>
        <label className={styles.checkbox}><input name="requiresDesktop" type="checkbox" defaultChecked={Boolean(editing?.budget?.requiresDesktop)} />需要共享桌面（按 FIFO 单执行者排队）</label>
        <p className={styles.muted}>token 上限仅在平台能给出真实用量时生效；没有计量的历史执行会阻止继续派发，而不是当作 0。</p>
      </fieldset>
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
