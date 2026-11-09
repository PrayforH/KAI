"use client";

import { useState } from "react";
import { assistantRequest, type ObjectiveSchedule } from "../lib/assistant-client";
import { buildCron, readCronFrequency, WEEKDAY_LABELS } from "../lib/automation-schedule";
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
  const storedCron = duty.schedule?.expression ?? "";
  const reading = readCronFrequency(storedCron, 1);
  const fixed = reading?.frequency === "daily" || reading?.frequency === "weekly" ? reading : null;
  const stored = duty.schedule?.meetingStartsAt ? "meeting" : fixed?.frequency ??
    (duty.schedule?.kind === "once" || duty.schedule?.kind === "cron"
      ? duty.schedule.kind : String(storedMinutes ?? ""));
  const storedAt = localDateTime(duty.schedule?.meetingStartsAt ?? duty.schedule?.at);
  const storedZone = duty.schedule?.timezone ?? "Asia/Shanghai";
  const storedOffset = duty.schedule?.minutesBefore ?? 1440;
  const [mode, setMode] = useState(stored);
  const [at, setAt] = useState(storedAt);
  const [cron, setCron] = useState(storedCron);
  const [zone, setZone] = useState(storedZone);
  const [time, setTime] = useState(fixed?.time ?? "09:00");
  const [weekday, setWeekday] = useState(fixed?.weekday ?? 5);
  const [minutesBefore, setMinutesBefore] = useState(storedOffset);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  const editable = duty.status === "active" || duty.status === "paused";
  const choices = [...new Set([60, 360, 1440, ...(storedMinutes ? [storedMinutes] : [])])];
  const fixedTime = mode === "daily" || mode === "weekly";
  const calendarTime = mode === "once" || mode === "meeting";
  const dirty = mode !== stored || (calendarTime && at !== storedAt)
    || (mode === "meeting" && minutesBefore !== storedOffset)
    || (fixedTime && (time !== fixed?.time || (mode === "weekly" && weekday !== fixed?.weekday)))
    || (mode === "cron" && cron !== storedCron)
    || ((fixedTime || mode === "cron") && zone !== storedZone);
  const incomplete = (calendarTime && !at)
    || (mode === "meeting" && (!Number.isInteger(minutesBefore) || minutesBefore < 1 || minutesBefore > 10_080))
    || (mode === "cron" && !cron.trim())
    || ((fixedTime || mode === "cron") && !zone.trim())
    || (fixedTime && !time);
  async function save() {
    if (busy || !editable || !duty.revision || incomplete) return;
    setBusy(true); setError(""); setSaved(false);
    try {
      const schedule: ObjectiveSchedule | null = mode === "meeting"
        ? { kind: "once", meetingStartsAt: new Date(at).toISOString(), minutesBefore, timezone: storedZone }
        : mode === "once"
          ? { kind: "once", at: new Date(at).toISOString(), timezone: storedZone }
          : mode === "cron" || fixedTime
            ? { kind: "cron", expression: fixedTime ? buildCron(mode, time, weekday) : cron.trim(), timezone: zone.trim() }
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
        <option value="daily">每天固定时间</option>
        <option value="weekly">每周固定时间</option>
        <option value="meeting">会议前提醒</option>
        <option value="cron">按 cron 周期执行</option>
      </select>
    </label>
    {calendarTime && <label>
      <span>{mode === "meeting" ? "会议开始时间（浏览器时区）" : "执行时间（浏览器时区）"}</span>
      <input type="datetime-local" value={at} disabled={busy || !editable}
        onChange={event => { setAt(event.target.value); setSaved(false); }} />
    </label>}
    {mode === "meeting" && <label>
      <span>提前分钟数</span>
      <input type="number" min={1} max={10080} value={minutesBefore} disabled={busy || !editable}
        onChange={event => { setMinutesBefore(Number(event.target.value)); setSaved(false); }} />
    </label>}
    {fixedTime && <label>
      <span>固定时刻</span>
      <input type="time" value={time} disabled={busy || !editable}
        onChange={event => { setTime(event.target.value); setSaved(false); }} />
    </label>}
    {mode === "weekly" && <label>
      <span>星期</span>
      <select value={weekday} disabled={busy || !editable}
        onChange={event => { setWeekday(Number(event.target.value)); setSaved(false); }}>
        {WEEKDAY_LABELS.map((label, day) => <option value={day} key={label}>{label}</option>)}
      </select>
    </label>}
    {mode === "cron" && (
      <label><span>Cron 表达式</span>
        <input value={cron} placeholder="0 9 * * 1-5" disabled={busy || !editable}
          onChange={event => { setCron(event.target.value); setSaved(false); }} />
      </label>
    )}
    {(mode === "cron" || fixedTime) && (
      <label><span>时区</span>
        <input value={zone} placeholder="Asia/Shanghai" disabled={busy || !editable}
          onChange={event => { setZone(event.target.value); setSaved(false); }} />
      </label>
    )}
    {duty.nextCheckAt && <small>下次执行 {new Date(duty.nextCheckAt).toLocaleString("zh-CN")}</small>}
    <small>只影响这项任务；保存规则不会立即重跑。</small>
    <button type="button" disabled={busy || !editable || !dirty || incomplete || !duty.revision}
      onClick={() => void save()}>{busy ? "正在保存…" : "保存执行方式"}</button>
    {error && <p role="alert">{error}</p>}
    {saved && <p role="status">执行方式已保存</p>}
  </div>;
}
