const MINUTE_SECONDS = 60;
const HOUR_MINUTES = 60;
const DAY_HOURS = 24;
const MONTH_DAYS = 30;
const YEAR_MONTHS = 12;

/**
 * Relative age for a task row, matching the way ZCode labels its own list:
 * 刚刚 / 12 分 / 21 小时 / 1 天, then 个月 and 年 for the long tail.
 */
export function formatTaskAge(value: string, now: number = Date.now()): string {
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return "";
  const seconds = Math.max(0, Math.round((now - then) / 1000));
  if (seconds < MINUTE_SECONDS) return "刚刚";
  const minutes = Math.floor(seconds / MINUTE_SECONDS);
  if (minutes < HOUR_MINUTES) return `${minutes} 分`;
  const hours = Math.floor(minutes / HOUR_MINUTES);
  if (hours < DAY_HOURS) return `${hours} 小时`;
  const days = Math.floor(hours / DAY_HOURS);
  if (days < MONTH_DAYS) return `${days} 天`;
  const months = Math.floor(days / MONTH_DAYS);
  if (months < YEAR_MONTHS) return `${months} 个月`;
  return `${Math.floor(months / YEAR_MONTHS)} 年`;
}

export type TaskTimeBucket = "today" | "yesterday" | "week" | "earlier";

export const TASK_TIME_BUCKET_LABELS: Readonly<Record<TaskTimeBucket, string>> = {
  today: "今天",
  yesterday: "昨天",
  week: "过去 7 天",
  earlier: "更早",
};

/**
 * Recency bucket for the task list's primary view, matching the way ChatGPT and
 * Claude group recent conversations. Days are compared by calendar day in the
 * reader's local timezone, so "昨天" starts at local midnight.
 */
export function taskTimeBucket(value: string, now: number = Date.now()): TaskTimeBucket {
  const then = new Date(value).getTime();
  if (Number.isNaN(then)) return "earlier";
  const startOfToday = new Date(now);
  startOfToday.setHours(0, 0, 0, 0);
  const startOfYesterday = new Date(startOfToday);
  startOfYesterday.setDate(startOfYesterday.getDate() - 1);
  const startOfWeek = new Date(startOfToday);
  startOfWeek.setDate(startOfWeek.getDate() - 6);
  if (then >= startOfToday.getTime()) return "today";
  if (then >= startOfYesterday.getTime()) return "yesterday";
  // "过去 7 天" covers the six full days before today, so a task shown under
  // 今天 never also appears in this bucket.
  if (then >= startOfWeek.getTime()) return "week";
  return "earlier";
}
