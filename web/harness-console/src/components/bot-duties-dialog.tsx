"use client";

import { useEffect, useState } from "react";
import { assistantRequest, type Execution } from "../lib/assistant-client";
import { taskThreadHref } from "../lib/conversation-navigation";
import { DutyScheduleControl, type BotDuty } from "./duty-schedule-control";
import styles from "./bot-edit-dialog.module.css";

type Duty = BotDuty & { hasExecutions?: boolean; latestExecution?: Pick<Execution, "status" | "summary"> | null };
const dutyStatus: Record<string, string> = {
  active: "进行中", paused: "已暂停", completed: "已完成", cancelled: "已停止",
};
const executionStatus: Record<Execution["status"], string> = {
  pending: "排队中", running: "执行中", succeeded: "已完成", failed: "执行失败", cancelled: "已取消",
};

export function DutyHistory({ objectiveId }: { objectiveId: string }) {
  const [executions, setExecutions] = useState<Execution[] | null>(null);
  const [failed, setFailed] = useState(false);
  return <details onToggle={event => {
    if (!event.currentTarget.open) return;
    setFailed(false);
    void assistantRequest<Execution[]>(`objectives/${encodeURIComponent(objectiveId)}/executions`)
      .then(setExecutions).catch(() => setFailed(true));
  }}>
    <summary>执行记录</summary>
    {failed ? <p role="alert">执行记录暂时无法读取</p> : executions === null ? <p>正在读取…</p> : (
      executions.length ? <ul>{executions.map(execution => <li key={execution.executionId}>
        <span>{new Date(execution.createdAt).toLocaleString("zh-CN")} · {executionStatus[execution.status]}</span>
        {execution.summary && <p>{execution.summary}</p>}
        {execution.runId && <a href={taskThreadHref(execution.threadId, execution.runId)}>打开执行结果</a>}
      </li>)}</ul> : <p>暂无执行记录</p>
    )}
  </details>;
}

export function BotDuties({ specialistId, compact = false }: { specialistId?: string; compact?: boolean }) {
  const [duties, setDuties] = useState<Duty[] | null>(null);
  const [error, setError] = useState("");
  const [changing, setChanging] = useState<string | null>(null);
  useEffect(() => {
    let current = true;
    void assistantRequest<{ duties: Duty[] }>(
      specialistId ? `specialists/${encodeURIComponent(specialistId)}` : "primary/duties",
    )
      .then(data => { if (current) setDuties(data.duties); })
      .catch(cause => { if (current) setError(cause instanceof Error ? cause.message : "读取职责失败"); });
    return () => { current = false; };
  }, [specialistId]);

  function replace(updated: BotDuty) {
    setDuties(items => items?.map(item => item.objectiveId === updated.objectiveId
      ? { ...item, ...updated } : item) ?? null);
  }

  async function change(duty: Duty, action: "pause" | "resume") {
    if (changing) return;
    setChanging(duty.objectiveId);
    setError("");
    try {
      replace(await assistantRequest<BotDuty>(
        `objectives/${encodeURIComponent(duty.objectiveId)}/actions`,
        "POST", { action, expectedRevision: duty.revision },
      ));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "计划状态未更新");
    } finally {
      setChanging(null);
    }
  }

  if (compact) {
    return <section className={styles.routines} aria-label="职责">
      <h3 className={styles.compactHeading}>职责</h3>
      {error && <p role="alert">{error}</p>}
      {duties?.length === 0 && <p className={styles.routineState}>暂无职责</p>}
      {duties?.map(duty => <section key={duty.objectiveId}>
        <div className={`${styles.routine} ${styles.compactRoutine}`}>
        <div className={styles.routineCopy}>
          <strong>{duty.title}</strong><small>{dutyStatus[duty.status]}</small>
        </div>
        {duty.schedule && (duty.status === "active" || duty.status === "paused") &&
          <button type="button" role="switch" className={styles.routineToggle} aria-label={duty.title}
          aria-checked={duty.status === "active"} disabled={changing !== null}
          onClick={() => void change(duty, duty.status === "active" ? "pause" : "resume")} />}
        </div>
        {duty.hasExecutions && <DutyHistory objectiveId={duty.objectiveId} />}
      </section>)}
    </section>;
  }

  return <div className={styles.routines}>
      {error && <p role="alert">{error}</p>}
      {!duties && !error && <p role="status">正在加载职责…</p>}
      {duties?.length === 0 && <p>这个 Bot 还没有职责。</p>}
      {duties?.map(duty => <section className={styles.routines} key={duty.objectiveId} aria-label={duty.title}>
        <div className={styles.routine}>
          <div className={styles.routineCopy}>
            <strong>{duty.title}</strong>
            {duty.latestExecution && <small>最近执行 {executionStatus[duty.latestExecution.status]}</small>}
          </div>
          <span className={styles.routineState}>{dutyStatus[duty.status]}</span>
        </div>
        {duty.latestExecution?.summary && <p>{duty.latestExecution.summary}</p>}
        {duty.hasExecutions && <DutyHistory objectiveId={duty.objectiveId} />}
        <DutyScheduleControl duty={duty} onSaved={replace} />
        {duty.schedule && (duty.status === "active" || duty.status === "paused") &&
          <button type="button" className={styles.primary} disabled={changing !== null}
            onClick={() => void change(duty, duty.status === "active" ? "pause" : "resume")}>
            {duty.status === "active" ? "暂停职责" : "恢复职责"}
          </button>}
      </section>)}
  </div>;
}
