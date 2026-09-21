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

/** How a schedule reads in the task list, without claiming more than the cron says. */
export function scheduleSummary(task: ApiAutomationTask): string {
  const schedule = task.schedule;
  if (schedule.type === "once" && schedule.at) {
    return `单次 ${formatScheduleTime(schedule.at)}`;
  }
  const cron = schedule.cron ?? "";
  const parts = cron.trim().split(/\s+/);
  if (parts.length !== 5) return cron;
  const [minute, hour, dayOfMonth, month, weekday] = parts;
  const unconstrained = (value: string) => value === "*" || value === "?";
  // A day-of-month or month constraint makes the schedule a monthly/annual one, which
  // "每天"/"每周" would misdescribe; those keep their expression.
  if (!unconstrained(dayOfMonth) || !unconstrained(month)) return cron;
  if (hour.startsWith("*/")) {
    const interval = Number(hour.slice(2));
    if (weekday === "1-5") return `工作日每 ${interval} 小时`;
    if (unconstrained(weekday)) return `每天每 ${interval} 小时`;
    return cron;
  }
  if (!/^\d{1,2}$/.test(minute) || !/^\d{1,2}$/.test(hour)) return cron;
  const timeText = `${pad(Number(hour))}:${pad(Number(minute))}`;
  if (unconstrained(weekday)) return `每天 ${timeText}`;
  if (!/^[0-6]$/.test(weekday)) return cron;
  // WEEKDAY_LABELS already carry the 周/日 prefix: "每周五", not "每周周五".
  return `每${WEEKDAY_LABELS[Number(weekday) % 7]} ${timeText}`;
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
