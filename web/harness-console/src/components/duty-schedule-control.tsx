"use client";

import { useEffect, useState } from "react";
import { assistantRequest, type ObjectiveSchedule } from "../lib/assistant-client";
import {
  buildDutyCron,
  readDutyRepeat,
  WEEKDAY_LABELS,
  type DutyRepeatReading,
} from "../lib/automation-schedule";
import styles from "./bot-edit-dialog.module.css";

export type BotDuty = {
  objectiveId: string;
  title: string;
  status: string;
  revision: number;
  nextCheckAt?: string | null;
  schedule: ObjectiveSchedule | null;
};

/** The brief fields a full objective update has to carry back unchanged. */
export const BRIEF_FIELDS = [
  "title", "instructions", "agentName", "agentVersion", "projectId",
  "completionCriteria", "notificationMode", "budget",
] as const;

export function localDateTime(value?: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16);
}

export function localDate(value?: string | null): string {
  if (!value) return "";
  const date = new Date(value);
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 10);
}

/** The repeat/validity fields as one draft, so an editor can embed them and save
 *  alongside the rest of the brief instead of patching the schedule on its own. */
export function useDutyScheduleDraft(duty: BotDuty) {
  const storedMinutes = duty.schedule?.intervalMinutes;
  const storedCron = duty.schedule?.expression ?? "";
  const reading = duty.schedule?.kind === "cron" ? readDutyRepeat(storedCron) : null;
  const storedMode = duty.schedule?.meetingStartsAt ? "meeting"
    : duty.schedule?.kind === "once" ? "once"
      : duty.schedule?.kind === "cron" ? reading?.repeat ?? "cron"
        : storedMinutes ? String(storedMinutes) : "";
  const storedAt = localDateTime(duty.schedule?.meetingStartsAt ?? duty.schedule?.at);
  const storedZone = duty.schedule?.timezone ?? "Asia/Shanghai";
  const storedOffset = duty.schedule?.minutesBefore ?? 1440;
  const [mode, setMode] = useState(storedMode);
  const [at, setAt] = useState(storedAt);
  const [cron, setCron] = useState(storedCron);
  const [zone, setZone] = useState(storedZone);
  const [time, setTime] = useState(reading && "time" in reading ? reading.time : "09:00");
  const [weekday, setWeekday] = useState(reading?.repeat === "weekly" ? reading.weekday : 5);
  const [dayOfMonth, setDayOfMonth] = useState(
    reading?.repeat === "monthly" || reading?.repeat === "yearly" ? reading.dayOfMonth : 1,
  );
  const [month, setMonth] = useState(reading?.repeat === "yearly" ? reading.month : 1);
  const [minutesBefore, setMinutesBefore] = useState(storedOffset);
  // 有效期 lives on the Objective, not the schedule: read it before offering the field.
  const [objective, setObjective] = useState<Record<string, unknown> | null>(null);
  const [storedUntil, setStoredUntil] = useState("");
  const [until, setUntil] = useState("");
  const [validity, setValidity] = useState<"forever" | "until">("forever");
  const editable = duty.status === "active" || duty.status === "paused";
  const choices = [...new Set([60, 360, 1440, ...(storedMinutes ? [storedMinutes] : [])])];
  const calendarTime = mode === "once" || mode === "meeting";
  const clockTime = mode === "daily" || mode === "weekly" || mode === "monthly" || mode === "yearly";
  const validityChanged = validity !== (storedUntil ? "until" : "forever")
    || (validity === "until" && until !== storedUntil);

  useEffect(() => {
    let live = true;
    void assistantRequest<Record<string, unknown>>(`objectives/${encodeURIComponent(duty.objectiveId)}`)
      .then(item => {
        if (!live) return;
        setObjective(item);
        const ends = typeof item.endsAt === "string" ? localDate(item.endsAt) : "";
        setStoredUntil(ends);
        setUntil(ends);
        setValidity(ends ? "until" : "forever");
      })
      .catch(() => undefined);
    return () => { live = false; };
  }, [duty.objectiveId]);

  const dirty = mode !== storedMode || validityChanged
    || (calendarTime && at !== storedAt)
    || (mode === "meeting" && minutesBefore !== storedOffset)
    || (clockTime && time !== (reading && "time" in reading ? reading.time : "09:00"))
    || (mode === "weekly" && weekday !== (reading?.repeat === "weekly" ? reading.weekday : 5))
    || ((mode === "monthly" || mode === "yearly")
      && dayOfMonth !== (reading?.repeat === "monthly" || reading?.repeat === "yearly" ? reading.dayOfMonth : 1))
    || (mode === "yearly" && month !== (reading?.repeat === "yearly" ? reading.month : 1))
    || (mode === "cron" && cron !== storedCron)
    || ((clockTime || mode === "cron") && zone !== storedZone);
  const incomplete = (calendarTime && !at)
    || (mode === "meeting" && (!Number.isInteger(minutesBefore) || minutesBefore < 1 || minutesBefore > 10_080))
    || (mode === "cron" && !cron.trim())
    || (clockTime && !time)
    || (validity === "until" && !until)
    || ((clockTime || mode === "cron") && !zone.trim());
  function nextSchedule(): ObjectiveSchedule | null {
    if (mode === "meeting") {
      return { kind: "once", meetingStartsAt: new Date(at).toISOString(), minutesBefore, timezone: storedZone };
    }
    if (mode === "once") return { kind: "once", at: new Date(at).toISOString(), timezone: storedZone };
    if (mode === "cron") return { kind: "cron", expression: cron.trim(), timezone: zone.trim() };
    if (clockTime) {
      const repeat: DutyRepeatReading = mode === "daily" ? { repeat: "daily", time }
        : mode === "weekly" ? { repeat: "weekly", time, weekday }
          : mode === "monthly" ? { repeat: "monthly", time, dayOfMonth }
            : { repeat: "yearly", time, dayOfMonth, month };
      return { kind: "cron", expression: buildDutyCron(repeat), timezone: zone.trim() };
    }
    return mode ? { intervalMinutes: Number(mode), timezone: storedZone } : null;
  }
  return {
    duty, objective, editable, choices, calendarTime, clockTime, dirty, incomplete,
    validityChanged, storedUntil, nextSchedule,
    mode, setMode, at, setAt, cron, setCron, zone, setZone, time, setTime,
    weekday, setWeekday, dayOfMonth, setDayOfMonth, month, setMonth,
    minutesBefore, setMinutesBefore, validity, setValidity, until, setUntil,
  };
}

export type DutyScheduleDraft = ReturnType<typeof useDutyScheduleDraft>;

/** The repeat form itself. The caller decides what 保存 means. */
export function DutyScheduleFields({ draft, disabled = false }: {
  draft: DutyScheduleDraft;
  disabled?: boolean;
}) {
  const {
    duty, mode, setMode, at, setAt, cron, setCron, zone, setZone, time, setTime,
    weekday, setWeekday, dayOfMonth, setDayOfMonth, month, setMonth, minutesBefore,
    setMinutesBefore, validity, setValidity, until, setUntil,
    editable, choices, calendarTime, clockTime,
  } = draft;
  const locked = disabled || !editable;
  return <>
    <label>
      <span>重复</span>
      <select aria-label={`${duty.title}的重复方式`} value={mode} disabled={locked}
        onChange={event => setMode(event.target.value)}>
        <option value="">按需交办</option>
        <option value="once">单次</option>
        <option value="daily">每天</option>
        <option value="weekly">每周</option>
        <option value="monthly">每月</option>
        <option value="yearly">每年</option>
        {choices.map(value => <option key={value} value={value}>每 {value} 分钟</option>)}
        <option value="meeting">会议前提醒</option>
        <option value="cron">自定义 cron</option>
      </select>
    </label>
    {calendarTime && <label>
      <span>{mode === "meeting" ? "会议开始时间（浏览器时区）" : "执行时间（浏览器时区）"}</span>
      <input type="datetime-local" value={at} disabled={locked}
        onChange={event => setAt(event.target.value)} />
    </label>}
    {mode === "meeting" && <label>
      <span>提前分钟数</span>
      <input type="number" min={1} max={10080} value={minutesBefore} disabled={locked}
        onChange={event => setMinutesBefore(Number(event.target.value))} />
    </label>}
    {clockTime && <label>
      <span>时间</span>
      <input type="time" value={time} disabled={locked}
        onChange={event => setTime(event.target.value)} />
    </label>}
    {mode === "weekly" && <label>
      <span>星期</span>
      <select value={weekday} disabled={locked}
        onChange={event => setWeekday(Number(event.target.value))}>
        {WEEKDAY_LABELS.map((label, day) => <option value={day} key={label}>{label}</option>)}
      </select>
    </label>}
    {(mode === "monthly" || mode === "yearly") && <label>
      <span>日期</span>
      <span className={styles.dutyDate}>
        {mode === "yearly" && <select aria-label="月份" value={month} disabled={locked}
          onChange={event => setMonth(Number(event.target.value))}>
          {Array.from({ length: 12 }, (_, index) => index + 1).map(value => (
            <option value={value} key={value}>{value} 月</option>
          ))}
        </select>}
        <input aria-label="日" type="number" min={1} max={31} value={dayOfMonth} disabled={locked}
          onChange={event => setDayOfMonth(Number(event.target.value))} /> 日
      </span>
    </label>}
    {mode === "cron" && (
      <label><span>Cron 表达式</span>
        <input value={cron} placeholder="0 9 * * 1-5" disabled={locked}
          onChange={event => setCron(event.target.value)} />
      </label>
    )}
    {(clockTime || mode === "cron") && (
      <label><span>时区</span>
        <input value={zone} placeholder="Asia/Shanghai" disabled={locked}
          onChange={event => setZone(event.target.value)} />
      </label>
    )}
    <label>
      <span>有效期</span>
      <select aria-label={`${duty.title}的有效期`} value={validity} disabled={locked}
        onChange={event => setValidity(event.target.value as "forever" | "until")}>
        <option value="forever">长期有效</option>
        <option value="until">截止日期</option>
      </select>
    </label>
    {validity === "until" && <label>
      <span>截止到</span>
      <input type="date" value={until} disabled={locked}
        onChange={event => setUntil(event.target.value)} />
    </label>}
    <small>每月 31 日或 2 月 29 日在没有该日期的月份跳过。</small>
  </>;
}

/** Trigger edits update one owned Objective with CAS; they never start a Run.
 *  The repeat vocabulary follows the prototype: 单次/每天/每周/每月/每年 plus a
 *  validity window; intervals, meeting reminders and raw cron stay available. */
export function DutyScheduleControl({ duty, onSaved }: {
  duty: BotDuty;
  onSaved: (duty: BotDuty) => void;
}) {
  const draft = useDutyScheduleDraft(duty);
  const { editable, dirty, incomplete, validityChanged, objective, nextSchedule } = draft;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);
  async function save() {
    if (busy || !editable || !duty.revision || incomplete) return;
    setBusy(true); setError(""); setSaved(false);
    try {
      const schedule = nextSchedule();
      let updated: BotDuty;
      if (validityChanged) {
        // endsAt only travels with the whole brief, so merge the change into the
        // objective as it stands now and let the revision guard concurrent edits.
        const current = objective
          ?? await assistantRequest<Record<string, unknown>>(`objectives/${encodeURIComponent(duty.objectiveId)}`);
        const brief = Object.fromEntries(BRIEF_FIELDS.map(field => [field, current[field] ?? null]));
        const response = await assistantRequest<Record<string, unknown>>(
          `objectives/${encodeURIComponent(duty.objectiveId)}`, "PUT", {
            ...brief,
            schedule,
            endsAt: draft.validity === "until" ? new Date(`${draft.until}T23:59:59`).toISOString() : null,
            expectedRevision: duty.revision,
          },
        );
        updated = {
          objectiveId: duty.objectiveId,
          title: String(response.title ?? duty.title),
          status: String(response.status ?? duty.status),
          revision: Number(response.revision ?? duty.revision + 1),
          schedule: (response.schedule ?? schedule) as ObjectiveSchedule | null,
          nextCheckAt: (response.nextCheckAt as string | null | undefined) ?? null,
        };
      } else {
        updated = await assistantRequest<BotDuty>(
          `objectives/${encodeURIComponent(duty.objectiveId)}/schedule`, "PATCH", {
            expectedRevision: duty.revision,
            schedule,
          },
        );
      }
      onSaved(updated); setSaved(true);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "执行规则未保存");
    } finally { setBusy(false); }
  }
  return <div className={styles.dutyTrigger}>
    <DutyScheduleFields draft={draft} disabled={busy} />
    {duty.nextCheckAt && <small>下次执行 {new Date(duty.nextCheckAt).toLocaleString("zh-CN")}</small>}
    <small>只影响这项任务；保存规则不会立即重跑。</small>
    <button type="button" disabled={busy || !editable || !dirty || incomplete || !duty.revision}
      onClick={() => void save()}>{busy ? "正在保存…" : "保存执行规则"}</button>
    {error && <p role="alert">{error}</p>}
    {saved && <p role="status">执行规则已保存</p>}
  </div>;
}
