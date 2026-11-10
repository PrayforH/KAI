/**
 * Automation scheduling: the form's frequencies, the cron expressions they mean, and
 * the reverse reading used when a task is opened for editing.
 *
 * The reverse reading is the part that has to be conservative. A form cannot express
 * every cron, so a schedule that is not exactly what one frequency produces stays
 * `custom` and keeps its expression: inferring a frequency from a partial match used
 * to rewrite an every-four-hours schedule into "weekdays every 2 hours" the moment a
 * task was opened and saved again.
 */

import type { ApiAutomationTask } from "./studio-client";

export type ScheduleFrequency = "once" | "daily" | "weekly" | "weekdays" | "custom";

export const WEEKDAY_LABELS = ["周日", "周一", "周二", "周三", "周四", "周五", "周六"];

/** What the weekday-every-two-hours template means, in one place. */
export const WEEKDAYS_CRON = "0 */2 * * 1-5";

export function pad(value: number): string {
  return String(value).padStart(2, "0");
}

export function localTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "Asia/Shanghai";
  } catch {
    return "Asia/Shanghai";
  }
}

export function localDatetimeValue(date: Date): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

export function formatDuration(ms: number | null | undefined): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms} ms`;
  const seconds = Math.round(ms / 1000);
  if (seconds < 60) return `${seconds} 秒`;
  return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

export function formatScheduleTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** The cron expression a frequency means. `custom` keeps the user's own expression. */
export function buildCron(frequency: ScheduleFrequency, time: string, weekday: number): string {
  const [hour, minute] = time.split(":").map((item) => Number(item) || 0);
  if (frequency === "daily") return `${minute} ${hour} * * *`;
  if (frequency === "weekly") return `${minute} ${hour} * * ${weekday}`;
  if (frequency === "weekdays") return WEEKDAYS_CRON;
  return "0 9 * * *";
}

/** What a cron means in form terms, or null when no form frequency produces it. */
export type ScheduleReading =
  | { frequency: "weekdays" }
  | { frequency: "daily" | "weekly"; time: string; weekday: number };

export function readCronFrequency(cron: string, weekday: number): ScheduleReading | null {
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return null;
  if (cron.trim() === WEEKDAYS_CRON) return { frequency: "weekdays" };
  const [minute, hour, , , weekdayField] = parts;
  if (!/^\d{1,2}$/.test(minute) || !/^\d{1,2}$/.test(hour)) return null;
  const time = `${pad(Number(hour))}:${pad(Number(minute))}`;
  if (cron.trim() === buildCron("daily", time, weekday)) {
    return { frequency: "daily", time, weekday };
  }
  if (/^[0-6]$/.test(weekdayField)) {
    const day = Number(weekdayField);
    if (cron.trim() === buildCron("weekly", time, day)) {
      return { frequency: "weekly", time, weekday: day };
    }
  }
  return null;
}

/** The repeats a duty (assistant 定时任务) offers, in the prototype's vocabulary. */
export type DutyRepeat = "once" | "daily" | "weekly" | "monthly" | "yearly";

export type DutyRepeatReading =
  | { repeat: "daily"; time: string }
  | { repeat: "weekly"; time: string; weekday: number }
  | { repeat: "monthly"; time: string; dayOfMonth: number }
  | { repeat: "yearly"; time: string; dayOfMonth: number; month: number };

/** The cron a repeat means. Monthly/annual carry an explicit day, because a
 *  five-field cron cannot derive it from "每月". */
export function buildDutyCron(reading: DutyRepeatReading): string {
  const [hour, minute] = reading.time.split(":").map((item) => Number(item) || 0);
  const clock = `${minute} ${hour}`;
  if (reading.repeat === "daily") return `${clock} * * *`;
  if (reading.repeat === "weekly") return `${clock} * * ${reading.weekday}`;
  if (reading.repeat === "monthly") return `${clock} ${reading.dayOfMonth} * *`;
  return `${clock} ${reading.dayOfMonth} ${reading.month} *`;
}

/** What a cron means in duty terms, or null when no repeat produces it. */
export function readDutyRepeat(cron: string): DutyRepeatReading | null {
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return null;
  const [minute, hour, dayOfMonth, month, weekday] = parts;
  if (!/^\d{1,2}$/.test(minute) || !/^\d{1,2}$/.test(hour)) return null;
  const time = `${pad(Number(hour))}:${pad(Number(minute))}`;
  if (dayOfMonth === "*" && month === "*") {
    if (weekday === "*") return { repeat: "daily", time };
    if (/^[0-7]$/.test(weekday)) return { repeat: "weekly", time, weekday: Number(weekday) % 7 };
    return null;
  }
  if (!/^\d{1,2}$/.test(dayOfMonth) || Number(dayOfMonth) < 1 || Number(dayOfMonth) > 31) return null;
  if (month === "*" && weekday === "*") {
    return { repeat: "monthly", time, dayOfMonth: Number(dayOfMonth) };
  }
  if (/^\d{1,2}$/.test(month) && Number(month) >= 1 && Number(month) <= 12 && weekday === "*") {
    return { repeat: "yearly", time, dayOfMonth: Number(dayOfMonth), month: Number(month) };
  }
  return null;
}

/** A cron as a person reads it, for lists and summaries. */
export function describeCron(cron: string): string | null {
  const reading = readDutyRepeat(cron);
  if (!reading) return null;
  if (reading.repeat === "daily") return `每天 ${reading.time}`;
  if (reading.repeat === "weekly") return `每${WEEKDAY_LABELS[reading.weekday]} ${reading.time}`;
  if (reading.repeat === "monthly") return `每月 ${reading.dayOfMonth} 日 ${reading.time}`;
  return `每年 ${reading.month} 月 ${reading.dayOfMonth} 日 ${reading.time}`;
}

/** How a schedule reads in the task list, without claiming more than the cron says. */
export function scheduleSummary(task: ApiAutomationTask): string {
  const schedule = task.schedule;
  if (schedule.type === "once" && schedule.at) {
    return `单次 ${formatScheduleTime(schedule.at)}`;
  }
  const cron = schedule.cron ?? "";
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return cron;
  const [minute, hour, , , weekday] = parts;
  const unconstrained = (value: string) => value === "*" || value === "?";
  if (hour.startsWith("*/")) {
    const interval = Number(hour.slice(2));
    if (weekday === "1-5") return `工作日每 ${interval} 小时`;
    if (unconstrained(weekday)) return `每天每 ${interval} 小时`;
    return cron;
  }
  if (!/^\d{1,2}$/.test(minute) || !/^\d{1,2}$/.test(hour)) return cron;
  // Daily, weekly, monthly and annual clocks all come from one reading, so the list
  // says the same thing the editor shows.
  return describeCron(cron) ?? cron;
}

/** The next moment the weekday-every-two-hours template actually fires. */
export function nextWeekdayEvenHour(now: Date): Date {
  for (let dayOffset = 0; dayOffset <= 7; dayOffset += 1) {
    const day = new Date(now);
    day.setDate(now.getDate() + dayOffset);
    if (day.getDay() === 0 || day.getDay() === 6) continue;
    for (let hour = 0; hour <= 23; hour += 2) {
      const candidate = new Date(day);
      candidate.setHours(hour, 0, 0, 0);
      if (candidate > now) return candidate;
    }
  }
  return now;
}
