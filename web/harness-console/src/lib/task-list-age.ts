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
