"use client";

import { type FormEvent, useCallback, useEffect, useState } from "react";
import { useAuth } from "../auth-provider";
import { useConfirmationDialog } from "../confirmation-dialog";
import {
  lifecycleClient,
  type DataLifecycleJob,
  type DataLifecycleOverview,
  type LifecycleScope,
  type RetentionPolicy,
} from "../../lib/studio-client";
import { createRandomId } from "../../lib/random-id";
import styles from "./data-lifecycle-control-plane.module.css";

const ADAPTER_LABELS: Record<string, string> = {
  "object-store": "对象存储",
  "sdk-session": "SDK 会话",
  memory: "长期记忆",
  langfuse: "Langfuse",
  postgresql: "PostgreSQL",
  "export-artifact": "导出制品",
};

const JOB_LABELS = { export: "导出", delete: "删除", retention: "自动保留" } as const;
const STATUS_LABELS = {
  queued: "排队中",
  running: "处理中",
  succeeded: "已完成",
  partial_failed: "部分失败",
  failed: "失败",
} as const;

function key(prefix: string) {
  return `${prefix}:${new Date().toISOString()}:${createRandomId()}`;
}

function Jobs({ jobs, refresh }: { jobs: DataLifecycleJob[]; refresh: () => Promise<void> }) {
  async function retry(jobId: string) {
    await lifecycleClient.retryJob(jobId);
    await refresh();
  }
  if (!jobs.length) {
    return <div className={styles.empty}><strong>还没有数据任务</strong><span>发起导出后，级联处理进度会显示在这里。</span></div>;
  }
  return <div className={styles.jobs}>{jobs.map((job) => (
    <article className={styles.job} key={job.jobId} data-status={job.status}>
      <header>
        <div><span>{JOB_LABELS[job.kind]}</span><strong>{job.scope.kind} / {job.scope.subjectId}</strong></div>
        <div className={styles.jobState}><strong>{STATUS_LABELS[job.status]}</strong><time>{new Date(job.createdAt).toLocaleString("zh-CN")}</time></div>
      </header>
      <div className={styles.cascade} aria-label="级联处理进度">
        {job.adapters.map((adapter) => <div key={adapter.adapter} data-state={adapter.status}>
          <i aria-hidden="true" />
          <span>{ADAPTER_LABELS[adapter.adapter] ?? adapter.adapter}</span>
          <small>{adapter.status === "failed" ? adapter.errorMessage : `${adapter.processedItems} 项`}</small>
        </div>)}
      </div>
      <footer>
        <code>{job.jobId}</code>
        <div>
          {(job.status === "failed" || job.status === "partial_failed") && <button type="button" onClick={() => void retry(job.jobId)}>重试失败步骤</button>}
          {job.status === "succeeded" && job.kind === "export" && <a href={`/api/data-lifecycle/jobs/${encodeURIComponent(job.jobId)}/artifact`}>下载 {job.exportFilename ?? "数据包"}</a>}
        </div>
      </footer>
    </article>
  ))}</div>;
}

export function DataLifecycleControlPlane({ embedded = false }: { embedded?: boolean }) {
  const { user, membership } = useAuth();
  const { requestConfirmation, confirmationDialog } = useConfirmationDialog();
  const canAdmin = membership.role === "owner" || membership.role === "admin";
  const [overview, setOverview] = useState<DataLifecycleOverview | null>(null);
  const [selfJobs, setSelfJobs] = useState<DataLifecycleJob[]>([]);
  const [policyDraft, setPolicyDraft] = useState<RetentionPolicy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      if (canAdmin) {
        const value = await lifecycleClient.overview();
        setOverview(value);
        setPolicyDraft(value.policy);
      } else {
        setSelfJobs(await lifecycleClient.selfJobs());
      }
      setError(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "数据治理控制面暂时不可用");
    }
  }, [canAdmin]);

  useEffect(() => { void load(); }, [load]);
  const jobs = canAdmin ? overview?.jobs ?? [] : selfJobs;
  useEffect(() => {
    if (!jobs.some((job) => job.status === "queued" || job.status === "running")) return;
    const timer = window.setInterval(() => void load(), 2500);
    return () => window.clearInterval(timer);
  }, [jobs, load]);

  async function savePolicy(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!policyDraft) return;
    setBusy(true);
    try {
      await lifecycleClient.replacePolicy(policyDraft, policyDraft);
      setNotice("保留策略已保存，新旧数据会按新版本执行。 ");
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "保留策略未能保存");
    } finally { setBusy(false); }
  }

  async function createHold(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const kind = String(form.get("kind")) as LifecycleScope["kind"];
    const subjectId = kind === "tenant" ? membership.tenant_id : String(form.get("subjectId"));
    setBusy(true);
    try {
      await lifecycleClient.createHold({ kind, subjectId }, String(form.get("reason")));
      event.currentTarget.reset();
      setNotice("Legal Hold 已生效，匹配范围的自动清理和删除会被阻止。");
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Legal Hold 未能创建");
    } finally { setBusy(false); }
  }

  async function run(kind: DataLifecycleJob["kind"], scope: LifecycleScope) {
    if (kind === "delete" && !(await requestConfirmation({
      title: "删除你的 Harness 数据？",
      description: "删除任务会级联处理会话、文件、记忆与观测副本，且无法恢复。Legal Hold、审计与部署证据仍按治理规则保留。",
      confirmLabel: "确认删除",
      context: <span>范围：当前用户 <code>{user.user_id}</code></span>,
      tone: "danger",
    }))) return;
    setBusy(true);
    try {
      await lifecycleClient.createJob(kind, scope, key(kind));
      setNotice(kind === "export" ? "导出已进入队列。" : kind === "delete" ? "删除已进入队列。" : "保留策略执行已进入队列。");
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "任务未能创建");
    } finally { setBusy(false); }
  }

  if (!overview && canAdmin && !error) return <div className={`${styles.state}${embedded ? ` ${styles.embeddedState}` : ""}`} aria-busy="true"><strong>正在读取数据边界</strong><span>核对保留策略、Legal Hold 与外部删除状态…</span></div>;

  return <div className={embedded ? `${styles.shell} ${styles.embedded}` : undefined}>
    <div className={styles.railCopy}><strong>数据生命周期</strong><p>导出与删除按外部系统顺序级联。任何失败都会留下可审计断点，不把“请求已受理”误报为“已删除”。</p></div>

    <section className={styles.content}>
      <header className={styles.header}><div><p>Data lifecycle ledger</p><h1>控制数据留下多久，以及如何离开</h1><span>覆盖 PostgreSQL、对象存储、SDK 会话、长期记忆与 Langfuse；审计和部署证据不会随业务数据一起消失。</span></div><div className={styles.headerActions}><button disabled={busy} onClick={() => void run("export", { kind: "user", subjectId: user.user_id })}>导出我的数据</button><button className={styles.danger} disabled={busy} onClick={() => void run("delete", { kind: "user", subjectId: user.user_id })}>删除我的数据</button></div></header>
      {notice && <p className={styles.notice} role="status">{notice}</p>}
      {error && <p className={styles.error} role="alert">{error}<button onClick={() => void load()}>重试</button></p>}

      {canAdmin && policyDraft && <section className={styles.policy}>
        <div className={styles.sectionCopy}><p>Retention policy · r{policyDraft.revision}</p><h2>保留周期</h2><span>每天由 Worker 幂等触发一次；Legal Hold 优先级始终更高。</span><button disabled={busy} onClick={() => void run("retention", { kind: "tenant", subjectId: membership.tenant_id })}>立即执行一次</button></div>
        <form onSubmit={savePolicy}>{([
          ["sessionDays", "会话与 Run", "天"], ["artifactDays", "制品与快照", "天"], ["traceDays", "Langfuse Trace", "天"], ["evalDays", "评测数据", "天"],
        ] as const).map(([field, label, unit]) => <label key={field}><span>{label}<small>{field}</small></span><span className={styles.input}><input type="number" min={1} value={policyDraft[field]} disabled={busy} onChange={(event) => setPolicyDraft({ ...policyDraft, [field]: Number(event.target.value) })}/><i>{unit}</i></span></label>)}<footer><span>使用 revision compare-and-set，避免覆盖其他管理员的修改。</span><button disabled={busy}>{busy ? "正在保存…" : "保存周期"}</button></footer></form>
      </section>}

      {canAdmin && overview && <section className={styles.holds}>
        <div className={styles.sectionCopy}><p>Legal hold</p><h2>冻结删除范围</h2><span>用于调查、诉讼或监管保全。释放动作同样写入审计。</span></div>
        <div><form onSubmit={createHold}><select name="kind" aria-label="范围类型"><option value="tenant">整个租户</option><option value="user">用户</option><option value="agent">智能体</option><option value="session">会话</option></select><input name="subjectId" placeholder="用户、智能体或会话 ID；租户范围可留空"/><input name="reason" required placeholder="冻结原因"/><button disabled={busy}>添加 Hold</button></form><div className={styles.holdList}>{overview.holds.length ? overview.holds.map((hold) => <article key={hold.holdId} data-active={hold.active}><div><strong>{hold.scope.kind} / {hold.scope.subjectId}</strong><span>{hold.reason}</span></div><small>{hold.active ? "生效中" : `已由 ${hold.releasedBy} 释放`}</small>{hold.active && <button onClick={async () => { await lifecycleClient.releaseHold(hold.holdId); await load(); }}>释放</button>}</article>) : <p>当前没有 Legal Hold。</p>}</div></div>
      </section>}

      <section className={styles.history}><div className={styles.historyHead}><div><p>Lifecycle jobs</p><h2>{canAdmin ? "租户处理记录" : "我的处理记录"}</h2></div><button onClick={() => void load()}>刷新</button></div><Jobs jobs={jobs} refresh={load}/></section>
    </section>
    {confirmationDialog}
  </div>;
}
