"use client";

import { useState } from "react";
import { assistantRequest, type ObjectiveSchedule } from "../lib/assistant-client";
import styles from "./bot-edit-dialog.module.css";

export type BotDuty = {
  objectiveId: string;
  title: string;
  status: string;
  revision: number;
  nextCheckAt?: string | null;
  schedule: ObjectiveSchedule | null;
};

function localDateTime(value?: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}

/** Trigger edits update one owned Objective with CAS; they never start a Run. */
export function DutyScheduleControl({ duty, onSaved }: {
  duty: BotDuty;
  onSaved: (duty: BotDuty) => void;
}) {
  const storedMinutes = duty.schedule?.intervalMinutes;
  const stored = duty.schedule?.kind === "once" || duty.schedule?.kind === "cron"
    ? duty.schedule.kind : String(storedMinutes ?? "");
  const storedAt = localDateTime(duty.schedule?.at);
  const storedCron = duty.schedule?.expression ?? "";
  const storedZone = duty.schedule?.timezone ?? "Asia/Shanghai";
  const [mode, setMode] = useState(stored);
  const [at, setAt] = useState(storedAt);
  const [cron, setCron] = useState(storedCron);
  const [zone, setZone] = useState(storedZone);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const editable = duty.status === "active" || duty.status === "paused";
  const choices = [...new Set([60, 360, 1440, ...(storedMinutes ? [storedMinutes] : [])])];
  const dirty = mode !== stored || (mode === "once" && at !== storedAt)
    || (mode === "cron" && (cron !== storedCron || zone !== storedZone));
  const incomplete = (mode === "once" && !at) || (mode === "cron" && (!cron.trim() || !zone.trim()));
  async function save() {
    if (busy || !editable || !duty.revision || incomplete) return;
    setBusy(true); setError(""); setSaved(false);
    try {
      const schedule = mode === "once"
        ? { kind: "once", at: new Date(at).toISOString(), timezone: storedZone }
        : mode === "cron" ? { kind: "cron", expression: cron.trim(), timezone: zone.trim() }
        : mode ? { intervalMinutes: Number(mode), timezone: storedZone } : null;
      const updated = await assistantRequest<BotDuty>(
        `objectives/${encodeURIComponent(duty.objectiveId)}/schedule`, "PATCH", {
          expectedRevision: duty.revision,
          schedule,
        },
      );
      onSaved(updated); setSaved(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "执行规则未保存");
    } finally { setBusy(false); }
  }
  return <div className={styles.dutyTrigger}>
    <label>
      <span>执行方式</span>
      <select aria-label={`${duty.title}的执行方式`} value={mode} disabled={busy || !editable}
        onChange={event => { setMode(event.target.value); setSaved(false); }}>
        <option value="">按需交办</option>
        {choices.map(value => <option key={value} value={value}>每 {value} 分钟</option>)}
        <option value="once">指定时间执行一次</option>
        <option value="cron">按 cron 周期执行</option>
      </select>
    </label>
    {mode === "once" && <label>
      <span>执行时间（浏览器时区）</span>
      <input type="datetime-local" value={at} disabled={busy || !editable}
        onChange={event => { setAt(event.target.value); setSaved(false); }} />
    </label>}
    {mode === "cron" && <>
      <label><span>Cron 表达式</span>
        <input value={cron} placeholder="0 9 * * 1-5" disabled={busy || !editable}
          onChange={event => { setCron(event.target.value); setSaved(false); }} />
      </label>
      <label><span>时区</span>
        <input value={zone} placeholder="Asia/Shanghai" disabled={busy || !editable}
          onChange={event => { setZone(event.target.value); setSaved(false); }} />
      </label>
    </>}
    {duty.nextCheckAt && <small>下次执行 {new Date(duty.nextCheckAt).toLocaleString("zh-CN")}</small>}
    <small>只影响这项任务；保存规则不会立即重跑。</small>
    <button type="button" disabled={busy || !editable || !dirty || incomplete || !duty.revision}
      onClick={() => void save()}>{busy ? "正在保存…" : "保存执行方式"}</button>
    {error && <p role="alert">{error}</p>}
    {saved && <p role="status">执行方式已保存</p>}
  </div>;
}
