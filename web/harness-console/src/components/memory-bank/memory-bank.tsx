"use client";
import { useInternalAgentsPreference } from "../../lib/interface-preferences";

import Link from "next/link";
import { useAuth } from "../auth-provider";
import { loadTaskAgentCatalog } from "../../lib/task-agent-catalog";
import { type FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { type MemoryEntry, type MemoryPolicy, type MemorySearchHit, memoryClient } from "../../lib/memory-client";
import {
  type ConfirmationRequester,
  useConfirmationDialog,
} from "../confirmation-dialog";
import styles from "./memory-bank.module.css";
import { ProductBrandCopy, ProductBrandMark } from "../product-brand";

const STATUS = { pending: "待确认", active: "使用中", rejected: "已拒绝", deleted: "已删除", expired: "已过期", superseded: "已替代" } as const;
const SENSITIVITY = { personal: "一般偏好", sensitive: "敏感信息", prohibited: "禁止保存" } as const;

function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value));
}

function EntryRow({ entry, busy, mutate, requestConfirmation }: { entry: MemoryEntry; busy: boolean; mutate: (action: () => Promise<unknown>, notice: string) => Promise<void>; requestConfirmation: ConfirmationRequester }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(entry.content);
  const visible = entry.status === "active" || entry.status === "pending";
  async function save(event: FormEvent) {
    event.preventDefault();
    await mutate(() => memoryClient.update(entry, draft.trim()), "记忆内容已更新。");
    setEditing(false);
  }
  async function remove() {
    const confirmed = await requestConfirmation({
      title: "删除这条长期记忆？",
      description: "删除后内容会立即清除，且不再提供给智能体检索。来源与删除审计仍会保留。",
      confirmLabel: "删除记忆",
      context: <span>智能体：<code>{entry.agentName}</code></span>,
      tone: "danger",
    });
    if (!confirmed) return;
    await mutate(() => memoryClient.remove(entry), "记忆已删除。");
  }
  return <article className={styles.entry} data-status={entry.status}>
    <header className={styles.entryHead}>
      <div className={styles.entryKind}><i aria-hidden="true"/><strong>{STATUS[entry.status]}</strong><span>{SENSITIVITY[entry.sensitivity]}</span></div>
      <time dateTime={entry.updatedAt}>{formatTime(entry.updatedAt)}</time>
    </header>
    {editing ? <form className={styles.edit} onSubmit={save}><textarea aria-label="记忆内容" value={draft} maxLength={4000} required onChange={(event) => setDraft(event.target.value)}/><div className={styles.actions}><button type="button" onClick={() => setEditing(false)}>取消</button><button className={styles.primary} disabled={busy || !draft.trim()}>保存修改</button></div></form> : <>
      <p className={`${styles.content} ${visible ? "" : styles.redacted}`}>{visible ? entry.content : "内容已清除，不会再提供给智能体。"}</p>
      {visible && entry.conditions && <p>适用条件：{entry.conditions}</p>}
      {visible && entry.supersedes && <p role="note">这是一条更新建议。确认后替代原记忆；拒绝则保留原记忆。</p>}
      {visible && entry.source.evidence && <details><summary>查看来源证据</summary><blockquote>{entry.source.evidence}</blockquote><small>会话：{entry.source.sessionId} · 版本 {entry.version}</small></details>}
      <div className={styles.meta}>
        {entry.topic && <span>主题 · {entry.topic}</span>}<span>来源 · {entry.source.label}</span><span>智能体 · {entry.agentName}</span><span>置信度 <b className={styles.confidence}><i style={{ width: `${entry.confidence * 100}%` }}/></b>{Math.round(entry.confidence * 100)}%</span>
        {entry.expiresAt && <span>到期 · {formatTime(entry.expiresAt)}</span>}
      </div>
      {visible && <div className={styles.actions}>
        {entry.status === "pending" && <><button className={styles.danger} disabled={busy} onClick={() => void mutate(() => memoryClient.reject(entry), "已拒绝这条记忆。")} >拒绝</button><button className={styles.primary} disabled={busy} onClick={() => void mutate(() => memoryClient.confirm(entry), "记忆已确认，后续对话可以使用。")} >确认保存</button></>}
        {entry.status === "active" && <><button disabled={busy} onClick={() => setEditing(true)}>编辑</button><button className={styles.danger} disabled={busy} onClick={() => void remove()}>删除</button></>}
      </div>}
    </>}
  </article>;
}

export function MemoryBank({ embedded = false }: { embedded?: boolean }) {
  const { user } = useAuth();
  const [showInternalAgents] = useInternalAgentsPreference();
  const Root = embedded ? "div" : "main";
  const [agentChoices, setAgentChoices] = useState<Array<{key: string; name: string; owner: string; label: string}>>([]);
  const loadSequence = useRef(0);
  const { requestConfirmation, confirmationDialog } = useConfirmationDialog();
  const [agentName, setAgentName] = useState("");
  const [ownerId, setOwnerId] = useState<string | undefined>();
  const [entries, setEntries] = useState<MemoryEntry[]>([]);
  const [policy, setPolicy] = useState<MemoryPolicy>({ consent: null, retention: null });
  const [retention, setRetention] = useState({ defaultDays: 180, maxDays: 365 });
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [searchHits, setSearchHits] = useState<MemorySearchHit[] | null>(null);
  const [proposal, setProposal] = useState("");
  async function search(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(null);
    try { setSearchHits(await memoryClient.search(agentName, query.trim(), ownerId)); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "检索失败"); }
    finally { setBusy(false); }
  }
  async function propose(event: FormEvent) {
    event.preventDefault();
    await mutate(() => memoryClient.propose(agentName, proposal.trim(), ownerId), "记忆建议已提交，请在记录中确认。");
    setProposal("");
  }

  useEffect(() => {
    let active = true;
    Promise.all([loadTaskAgentCatalog(user.user_id, showInternalAgents).catch(() => null), memoryClient.list()]).then(([catalog, records]) => {
      if (!active) return;
      const choices = new Map<string, {key: string; name: string; owner: string; label: string}>();
      for (const agent of catalog?.agents ?? []) {
        if (agent.canView === false) continue;
        const owner = agent.ownerUserId ?? user.user_id;
        const key = JSON.stringify([agent.name, owner]);
        if (!choices.has(key)) choices.set(key, {key, name: agent.name, owner, label: `${agent.displayName}${agent.spaceName ? ` · ${agent.spaceName}` : ""}`});
      }
      for (const entry of records) {
        const owner = entry.agentOwnerUserId ?? user.user_id;
        if (catalog?.hiddenAgents?.some((agent) => agent.name === entry.agentName && (agent.ownerUserId ?? user.user_id) === owner)) continue;
        const key = JSON.stringify([entry.agentName, owner]);
        if (!choices.has(key)) choices.set(key, {key, name: entry.agentName, owner, label: entry.agentName});
      }
      const next = [...choices.values()]; setAgentChoices(next);
      if (next[0]) { setAgentName(next[0].name); setOwnerId(next[0].owner); }
      else { setEntries([]); setLoading(false); }
    }).catch((caught) => { if (active) { setError(caught instanceof Error ? caught.message : "智能体目录暂时不可用"); setLoading(false); } });
    return () => { active = false; };
  }, [user.user_id, showInternalAgents]);

  const load = useCallback(async () => {
    if (!agentName) return;
    const sequence = ++loadSequence.current;
    setLoading(true); setEntries([]); setSearchHits(null); setPolicy({consent: null, retention: null});
    try {
      const [list, nextPolicy] = await Promise.all([memoryClient.list(agentName), memoryClient.policy(agentName, ownerId)]);
      if (sequence !== loadSequence.current) return;
      setEntries(list.filter((entry) => (entry.agentOwnerUserId ?? user.user_id) === (ownerId ?? user.user_id)));
      setPolicy(nextPolicy);
      setRetention({defaultDays: nextPolicy.retention?.defaultDays ?? 180, maxDays: nextPolicy.retention?.maxDays ?? 365});
      setError(null);
    } catch (caught) { if (sequence === loadSequence.current) setError(caught instanceof Error ? caught.message : "长期记忆暂时不可用"); }
    finally { if (sequence === loadSequence.current) setLoading(false); }
  }, [agentName, ownerId, user.user_id]);

  useEffect(() => { void load(); return () => { loadSequence.current++; }; }, [load]);
  async function mutate(action: () => Promise<unknown>, message: string) {
    setBusy(true); setNotice(null);
    try { await action(); setNotice(message); await load(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "操作未完成"); }
    finally { setBusy(false); }
  }
  async function toggleConsent() {
    if (!agentName) return;
    const allow = !(policy.consent?.allowAgentPersonal ?? false);
    await mutate(async () => { const consent = await memoryClient.saveConsent(agentName, policy, allow, ownerId); setPolicy({ ...policy, consent }); }, allow ? "已允许该智能体自动保存一般偏好。" : "已关闭自动保存，之后逐条确认。" );
  }
  async function saveRetention(event: FormEvent) {
    event.preventDefault();
    if (!agentName) return;
    await mutate(() => memoryClient.saveRetention(agentName, policy, retention.defaultDays, retention.maxDays, ownerId), "保留期限已更新。");
  }
  const counts = useMemo(() => ({ active: entries.filter((item) => item.status === "active").length, pending: entries.filter((item) => item.status === "pending").length, agents: new Set(entries.map((item) => item.agentName)).size }), [entries]);

  return <Root className={`${styles.shell}${embedded ? ` ${styles.embedded}` : ""}`} id={embedded ? undefined : "main-content"}>
    {!embedded && <header className={styles.topbar}><Link className={styles.brand} href="/"><ProductBrandMark /><ProductBrandCopy /></Link><div className={styles.topbarActions}><Link className={styles.back} href="/settings">返回设置</Link></div></header>}
    <div className={styles.frame}>
      <header className={styles.hero}><div><p>Managed memory ledger</p><h1>你决定智能体记住什么</h1><span>智能体只能提出记忆建议。默认逐条确认；敏感内容不会因为开启自动保存而绕过确认，密钥和提示注入内容始终拒绝保存。</span></div><div className={styles.scope}><label htmlFor="memory-agent">当前智能体</label><select id="memory-agent" value={JSON.stringify([agentName, ownerId])} disabled={busy || !agentChoices.length}
        onChange={(event) => { const choice = agentChoices.find((item) => item.key === event.target.value); if (choice) { setLoading(true); setAgentName(choice.name); setOwnerId(choice.owner); } }}>
        {!agentChoices.length && <option value="">暂无可用智能体</option>}
        {agentChoices.map((choice) => <option key={choice.key} value={choice.key}>{choice.label}</option>)}
      </select><small>选择智能体，查看其记忆与保存策略。</small></div></header>
      {error && <p className={styles.alert} role="alert">{error}</p>}{notice && <p className={`${styles.alert} ${styles.notice}`} role="status">{notice}</p>}
      <section className={styles.overview} aria-label="记忆概况"><div className={styles.stat}><span>正在使用</span><strong>{counts.active}</strong></div><div className={styles.stat} data-kind="pending"><span>等待确认</span><strong>{counts.pending}</strong></div><div className={styles.stat}><span>涉及智能体</span><strong>{counts.agents}</strong></div></section>
      <section className={styles.policy} aria-label="验证记忆召回">
        <h2>查找相关记忆</h2>
        <form onSubmit={search} className={styles.edit}>
          <label>用一个问题验证召回<input value={query} maxLength={500} placeholder="例如：我希望正式材料怎么写？" onChange={(event) => setQuery(event.target.value)}/></label>
          <button disabled={busy || loading || !agentName || !query.trim()}>检索记忆</button>
        </form>
        {searchHits !== null && <div aria-live="polite">{searchHits.length ? searchHits.map((hit) => <article key={hit.entry.entryId} className={styles.entry}><p>{hit.entry.content}</p><small>来源：{hit.entry.source.label} · 版本 {hit.entry.version} · 相关度 {Math.round(hit.score * 100)}%</small></article>) : <p>没有相关的有效记忆。待确认、已删除和已过期内容不会召回。</p>}</div>}
        <details><summary>手动添加记忆建议</summary><form onSubmit={propose} className={styles.edit}><textarea aria-label="新记忆内容" value={proposal} maxLength={4000} onChange={(event) => setProposal(event.target.value)}/><button disabled={busy || loading || !agentName || !proposal.trim()}>提交建议</button></form></details>
      </section>
      <div className={styles.workspace}>
        <section><header className={styles.ledgerHeader}><div><p className={styles.sectionLabel}>Memory entries</p><h2>记忆记录</h2></div><div className={styles.ledgerActions}><a href="/api/memory-bank/export" download="harness-memory.json">导出 JSON</a><button type="button" disabled={loading} onClick={() => void load()}>{loading ? "正在读取…" : "刷新"}</button></div></header><div className={styles.ledger}>{entries.length ? entries.map((entry) => <EntryRow key={entry.entryId} entry={entry} busy={busy} mutate={mutate} requestConfirmation={requestConfirmation}/>) : <div className={styles.empty}><strong>{loading ? "正在读取记忆" : "还没有记忆记录"}</strong><span>{loading ? "正在核对来源与授权状态…" : "当你或智能体提出需要长期保留的信息时，会显示在这里等待确认。"}</span></div>}</div></section>
        <aside className={styles.side}><section className={styles.policy}><p className={styles.sectionLabel}>Agent policy</p><h2>保存策略</h2><span>策略只作用于 <strong>{agentName || "尚未选择的智能体"}</strong>。</span><div className={styles.toggle}><div><strong>自动保存一般偏好</strong><small>敏感信息仍需逐条确认</small></div><button type="button" role="switch" aria-label="自动保存一般偏好" aria-pressed={policy.consent?.allowAgentPersonal ?? false} disabled={busy || loading || !agentName} onClick={() => void toggleConsent()}/></div><form onSubmit={saveRetention}><div className={styles.retention}><label>默认保留天数<input type="number" min={1} max={3650} value={retention.defaultDays} onChange={(event) => setRetention({ ...retention, defaultDays: Number(event.target.value) })}/></label><label>最长保留天数<input type="number" min={1} max={3650} value={retention.maxDays} onChange={(event) => setRetention({ ...retention, maxDays: Number(event.target.value) })}/></label></div><button className={styles.save} disabled={busy || loading || !agentName || retention.defaultDays > retention.maxDays}>{busy ? "正在保存…" : "保存保留期限"}</button></form></section><section className={styles.principles}><strong>记忆边界</strong><ul><li>每条记录保留来源、时间和置信度</li><li>编辑使用版本校验，避免覆盖并发修改</li><li>删除立即清空正文，并停止检索召回</li><li>不同租户、用户和智能体严格隔离</li></ul></section></aside>
      </div>
    </div>
    {confirmationDialog}
  </Root>;
}
