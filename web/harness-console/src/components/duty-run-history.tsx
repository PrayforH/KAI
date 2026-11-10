"use client";

import { useEffect, useMemo, useState } from "react";
import { assistantRequest, type Execution } from "../lib/assistant-client";
import { taskThreadHref } from "../lib/conversation-navigation";
import styles from "./bot-tasks.module.css";

const PAGE_SIZE = 10;
const statusLabel: Record<Execution["status"], string> = {
  pending: "排队中", running: "执行中", succeeded: "已完成", failed: "执行失败", cancelled: "已取消",
};

/** Trigger keys are namespaced by who started the run. The prototype offers
 *  one 类型 filter; these are the namespaces the server actually writes. */
export function triggerKind(key?: string): "schedule" | "manual" | "delegate" | "manage" | "other" {
  if (key?.startsWith("schedule:")) return "schedule";
  if (key?.startsWith("manual:")) return "manual";
  if (key?.startsWith("delegate:")) return "delegate";
  if (key?.startsWith("manage:")) return "manage";
  return "other";
}

const triggerLabel = { schedule: "定时", manual: "手动", delegate: "委派", manage: "管理", other: "其他" } as const;

export function filterExecutions(
  executions: Execution[],
  { status, trigger }: { status: string; trigger: string },
): Execution[] {
  return executions.filter(item => (status === "all" || item.status === status)
    && (trigger === "all" || triggerKind(item.triggerKey) === trigger));
}

/** 执行记录内页：状态/触发筛选 + 客户端分页，每条都能跳回它的会话。 */
export function DutyRunHistory({ objectiveId, title, scheduleText, onBack }: {
  objectiveId: string;
  title: string;
  scheduleText: string;
  onBack: () => void;
}) {
  const [runs, setRuns] = useState<Execution[] | null>(null);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("all");
  const [trigger, setTrigger] = useState("all");
  const [page, setPage] = useState(0);
  useEffect(() => {
    let live = true;
    void assistantRequest<Execution[]>(`objectives/${encodeURIComponent(objectiveId)}/executions`)
      .then(items => { if (live) setRuns(items); })
      .catch(cause => { if (live) setError(cause instanceof Error ? cause.message : "执行记录暂时无法读取"); });
    return () => { live = false; };
  }, [objectiveId]);
  const filtered = useMemo(
    () => filterExecutions(runs ?? [], { status, trigger }),
    [runs, status, trigger],
  );
  const pages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const current = Math.min(page, pages - 1);
  const visible = filtered.slice(current * PAGE_SIZE, current * PAGE_SIZE + PAGE_SIZE);

  return <section className={styles.runs} aria-label={`${title}的执行记录`}>
    <header className={styles.runsHead}>
      <button type="button" className={styles.back} onClick={onBack}>返回</button>
      <strong>执行记录</strong>
    </header>
    <div className={styles.runsSub}>
      <span className={styles.rowCopy}>
        <strong>{title}</strong>
        <small>{scheduleText}</small>
      </span>
      <div className={styles.filters}>
        <select aria-label="按状态筛选" value={status}
          onChange={event => { setStatus(event.target.value); setPage(0); }}>
          <option value="all">全部状态</option>
          <option value="succeeded">已完成</option>
          <option value="failed">执行失败</option>
          <option value="running">执行中</option>
          <option value="cancelled">已取消</option>
        </select>
        <select aria-label="按触发类型筛选" value={trigger}
          onChange={event => { setTrigger(event.target.value); setPage(0); }}>
          <option value="all">全部类型</option>
          <option value="schedule">定时</option>
          <option value="manual">手动</option>
          <option value="delegate">委派</option>
          <option value="manage">管理</option>
        </select>
      </div>
    </div>
    {error && <p role="alert" className={styles.error}>{error}</p>}
    {runs === null && !error && <p className={styles.hint}>正在读取执行记录…</p>}
    {runs && filtered.length === 0 && <p className={styles.hint}>{runs.length ? "没有符合筛选条件的记录" : "暂无执行记录"}</p>}
    {visible.map(run => <article key={run.executionId} className={styles.runRow}>
      <div className={styles.rowCopy}>
        <strong>{new Date(run.createdAt).toLocaleString("zh-CN")}</strong>
        <small>{triggerLabel[triggerKind(run.triggerKey)]} · {statusLabel[run.status]}</small>
        {run.summary && <p className={styles.runSummary}>{run.summary}</p>}
      </div>
      {run.runId
        ? <a className={styles.runLink} href={taskThreadHref(run.threadId, run.runId)}>在会话中定位</a>
        : <span className={styles.runLink} aria-disabled="true">尚未产生会话</span>}
    </article>)}
    {filtered.length > PAGE_SIZE && <nav className={styles.pager} aria-label="执行记录分页">
      <span>共 {filtered.length} 条 · 第 {current + 1}/{pages} 页</span>
      <button type="button" disabled={current === 0} onClick={() => setPage(current - 1)}>上一页</button>
      <button type="button" disabled={current >= pages - 1} onClick={() => setPage(current + 1)}>下一页</button>
    </nav>}
  </section>;
}
