import { describe, expect, it } from "vitest";
import { formatTaskAge } from "../src/lib/task-list-age";

const NOW = Date.parse("2026-09-17T12:00:00Z");
const ago = (seconds: number) => new Date(NOW - seconds * 1000).toISOString();

describe("task row relative age", () => {
  it("labels the recent past the way ZCode does", () => {
    expect(formatTaskAge(ago(0), NOW)).toBe("刚刚");
    expect(formatTaskAge(ago(59), NOW)).toBe("刚刚");
    expect(formatTaskAge(ago(60), NOW)).toBe("1 分");
    expect(formatTaskAge(ago(12 * 60), NOW)).toBe("12 分");
    expect(formatTaskAge(ago(59 * 60), NOW)).toBe("59 分");
    expect(formatTaskAge(ago(60 * 60), NOW)).toBe("1 小时");
    expect(formatTaskAge(ago(21 * 60 * 60), NOW)).toBe("21 小时");
    expect(formatTaskAge(ago(23 * 60 * 60), NOW)).toBe("23 小时");
    expect(formatTaskAge(ago(24 * 60 * 60), NOW)).toBe("1 天");
    expect(formatTaskAge(ago(29 * 24 * 60 * 60), NOW)).toBe("29 天");
  });

  it("keeps rolling into months and years", () => {
    expect(formatTaskAge(ago(30 * 24 * 60 * 60), NOW)).toBe("1 个月");
    expect(formatTaskAge(ago(300 * 24 * 60 * 60), NOW)).toBe("10 个月");
    expect(formatTaskAge(ago(365 * 24 * 60 * 60), NOW)).toBe("1 年");
  });

  it("treats clock skew as just now and rejects unparseable values", () => {
    expect(formatTaskAge(ago(-120), NOW)).toBe("刚刚");
    expect(formatTaskAge("not-a-date", NOW)).toBe("");
  });
});
