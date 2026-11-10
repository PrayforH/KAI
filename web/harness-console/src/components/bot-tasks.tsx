"use client";

import { useEffect, useState } from "react";
import { assistantRequest, type ObjectiveSchedule } from "../lib/assistant-client";
import { dutyScheduleText } from "./bot-duties-dialog";
import {
  BRIEF_FIELDS,
  DutyScheduleFields,
  useDutyScheduleDraft,
  type BotDuty,
} from "./duty-schedule-control";
import styles from "./bot-tasks.module.css";

type Duty = BotDuty & { hasExecutions?: boolean };
const dutyStatus: Record<string, string> = {
  active: "进行中", paused: "已暂停", completed: "已完成", cancelled: "已停止",
};

function whenLabel(value?: string | null): string {
  return value ? `下次执行 ${new Date(value).toLocaleString("zh-CN")}` : "";
}

/** The prototype's edit modal: name, requirements and repeat in one dialog that
 *  saves the whole brief at once; stop is the delete. */
export function DutyEditDialog({ duty, onClose, onSaved }: {
  duty: Duty;
  onClose: () => void;
  onSaved: (duty: BotDuty) => void;
}) {
  const draft = useDutyScheduleDraft(duty);
  const { objective, editable, dirty: scheduleDirty, incomplete, nextSchedule } = draft;
  const [title, setTitle] = useState(duty.title);
  const [instructions, setInstructions] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [armed, setArmed] = useState(false);

  useEffect(() => {
    if (!objective) return;
    if (typeof objective.instructions === "string") setInstructions(objective.instructions);
    if (typeof objective.title === "string") setTitle(objective.title);
  }, [objective]);

  useEffect(() => {
    const close = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, [onClose]);

  const dirty = scheduleDirty || title !== String(objective?.title ?? duty.title)
    || instructions !== String(objective?.instructions ?? "");
  const blocked = busy || !editable || !objective || !dirty || incomplete
    || !title.trim() || !instructions.trim();

  async function save() {
    if (blocked) return;
    setBusy(true); setError("");
    try {
      const base = objective ?? {};
      const brief = Object.fromEntries(BRIEF_FIELDS.map(field => [field, base[field] ?? null]));
      const response = await assistantRequest<Record<string, unknown>>(
        `objectives/${encodeURIComponent(duty.objectiveId)}`, "PUT", {
          ...brief,
          title: title.trim(),
          instructions: instructions.trim(),
          schedule: nextSchedule(),
          endsAt: draft.validity === "until" ? new Date(`${draft.until}T23:59:59`).toISOString() : null,
          expectedRevision: duty.revision,
        },
      );
      onSaved({
        objectiveId: duty.objectiveId,
        title: String(response.title ?? title.trim()),
        status: String(response.status ?? duty.status),
        revision: Number(response.revision ?? duty.revision + 1),
        schedule: (response.schedule ?? nextSchedule()) as ObjectiveSchedule | null,
        nextCheckAt: (response.nextCheckAt as string | null | undefined) ?? null,
      });
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "任务未保存");
    } finally { setBusy(false); }
  }

  async function act(action: "pause" | "resume" | "stop") {
    if (busy) return;
    setBusy(true); setError("");
    try {
      const response = await assistantRequest<Record<string, unknown>>(
        `objectives/${encodeURIComponent(duty.objectiveId)}/actions`, "POST", {
          action, expectedRevision: duty.revision,
        },
      );
      onSaved({
        objectiveId: duty.objectiveId,
        title: duty.title,
        status: String(response.status ?? (action === "stop" ? "cancelled" : duty.status)),
        revision: Number(response.revision ?? duty.revision + 1),
        schedule: (response.schedule ?? duty.schedule) as ObjectiveSchedule | null,
        nextCheckAt: (response.nextCheckAt as string | null | undefined) ?? null,
      });
      setArmed(false);
      if (action === "stop") onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "任务状态未更新");
    } finally { setBusy(false); }
  }

  return <div className={styles.backdrop} onClick={event => {
    if (event.target === event.currentTarget) onClose();
  }}>
    <div className={styles.panel} role="dialog" aria-modal="true" aria-label={`编辑任务 ${duty.title}`}>
      <button type="button" className={styles.close} aria-label="关闭" onClick={onClose}>×</button>
      <h2 className={styles.title}>编辑定时任务</h2>
      <label className={styles.field}>
        <span>名称</span>
        <input value={title} maxLength={40} disabled={busy || !editable}
          onChange={event => setTitle(event.target.value)} />
      </label>
      <label className={styles.field}>
        <span>任务要求</span>
        <textarea rows={4} value={instructions} maxLength={200} disabled={busy || !editable || !objective}
          placeholder={objective ? "写清要助理每次完成什么" : "正在读取任务…"}
          onChange={event => setInstructions(event.target.value)} />
      </label>
      <fieldset className={styles.cycle} disabled={busy || !editable}>
        <legend>执行周期</legend>
        <DutyScheduleFields draft={draft} disabled={busy} />
      </fieldset>
      {duty.nextCheckAt && <p className={styles.hint}>{whenLabel(duty.nextCheckAt)}</p>}
      {error && <p role="alert" className={styles.error}>{error}</p>}
      <footer className={styles.actions}>
        {editable && <button type="button" className={styles.danger} disabled={busy}
          onClick={() => armed ? void act("stop") : setArmed(true)}>
          {armed ? "确认删除？" : "删除任务"}
        </button>}
        {editable && <button type="button" className={styles.plain} disabled={busy}
          onClick={() => void act(duty.status === "active" ? "pause" : "resume")}>
          {duty.status === "active" ? "暂停" : "恢复"}
        </button>}
        <span className={styles.spacer} />
        <button type="button" className={styles.plain} disabled={busy} onClick={onClose}>取消</button>
        <button type="button" className={styles.primary} disabled={blocked} onClick={() => void save()}>
          {busy ? "正在保存…" : "保存"}
        </button>
      </footer>
    </div>
  </div>;
}

/** 任务 tab: this Bot's duties, one row each, opening the edit dialog. */
export function BotTasks({ specialistId }: { specialistId?: string }) {
  const [duties, setDuties] = useState<Duty[] | null>(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState<Duty | null>(null);
  useEffect(() => {
    let current = true;
    void assistantRequest<{ duties: Duty[] }>(
      specialistId ? `specialists/${encodeURIComponent(specialistId)}` : "primary/duties",
    )
      .then(data => { if (current) setDuties(data.duties); })
      .catch(cause => { if (current) setError(cause instanceof Error ? cause.message : "读取任务失败"); });
    return () => { current = false; };
  }, [specialistId]);

  function replace(updated: BotDuty) {
    setDuties(items => items?.map(item => item.objectiveId === updated.objectiveId
      ? { ...item, ...updated } : item) ?? null);
  }

  return <section className={styles.tasks} aria-label="任务">
    <header className={styles.head}>
      <h3>定时任务</h3>
      {duties && <span className={styles.count}>{duties.length}</span>}
    </header>
    {error && <p role="alert" className={styles.error}>{error}</p>}
    {duties === null && !error && <p className={styles.hint}>正在读取任务…</p>}
    {duties?.length === 0 && <p className={styles.hint}>暂无定时任务</p>}
    {duties?.map(duty => <button type="button" key={duty.objectiveId} className={styles.row}
      onClick={() => setEditing(duty)}>
      <span className={styles.rowCopy}>
        <strong>{duty.title}</strong>
        <small>{dutyScheduleText(duty.schedule)}</small>
        {duty.nextCheckAt && <small>{whenLabel(duty.nextCheckAt)}</small>}
      </span>
      <span className={styles.state} data-status={duty.status}>{dutyStatus[duty.status] ?? duty.status}</span>
    </button>)}
    {editing && <DutyEditDialog key={`${editing.objectiveId}-${editing.revision}`} duty={editing}
      onClose={() => setEditing(null)} onSaved={replace} />}
  </section>;
}
