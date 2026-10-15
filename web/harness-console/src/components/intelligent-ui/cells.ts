import type { PresentationCell } from "./schema";

export type ChartRow = Record<string, PresentationCell>;

/** One cell as text. A missing value is shown as an explicit dash rather than an
 * empty cell, so a gap in the data cannot be mistaken for a zero. */
export function cellText(cell: PresentationCell | undefined): string {
  if (cell === null || cell === undefined) return "—";
  if (typeof cell === "boolean") return cell ? "是" : "否";
  return String(cell);
}

export function isNumeric(cell: PresentationCell | undefined): cell is number {
  return typeof cell === "number" && Number.isFinite(cell);
}

/** Thousands-separated, capped at two decimals, optionally carrying its unit. */
export function formatNumber(value: string, unit?: string): string {
  if (value === "—") return value;
  const parsed = Number(value);
  const rendered = Number.isFinite(parsed)
    ? parsed.toLocaleString("zh-Hans-CN", { maximumFractionDigits: 2 })
    : value;
  return unit ? `${rendered}${unit}` : rendered;
}
