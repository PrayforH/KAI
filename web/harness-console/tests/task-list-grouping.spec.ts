import { describe, expect, it } from "vitest";
import { taskTimeBucket, TASK_TIME_BUCKET_LABELS } from "../src/lib/task-list-age";

// 2026-09-19 15:00 local time as the reference "now".
const now = new Date(2026, 8, 19, 15, 0, 0).getTime();

function at(year: number, month: number, day: number, hour = 12): string {
  return new Date(year, month - 1, day, hour, 0, 0).toISOString();
}

describe("task recency buckets", () => {
  it("labels the four buckets in Chinese", () => {
    expect(TASK_TIME_BUCKET_LABELS).toEqual({
      today: "今天",
      yesterday: "昨天",
      week: "过去 7 天",
      earlier: "更早",
    });
  });

  it("puts today's tasks in 今天", () => {
    expect(taskTimeBucket(at(2026, 9, 19, 0), now)).toBe("today");
    expect(taskTimeBucket(at(2026, 9, 19, 15), now)).toBe("today");
  });

  it("puts yesterday's tasks in 昨天 even across midnight", () => {
    expect(taskTimeBucket(at(2026, 9, 18, 23), now)).toBe("yesterday");
    expect(taskTimeBucket(at(2026, 9, 18, 0), now)).toBe("yesterday");
  });

  it("keeps the six full days before today in 过去 7 天", () => {
    expect(taskTimeBucket(at(2026, 9, 17), now)).toBe("week");
    expect(taskTimeBucket(at(2026, 9, 13), now)).toBe("week");
  });

  it("moves older tasks to 更早", () => {
    expect(taskTimeBucket(at(2026, 9, 12), now)).toBe("earlier");
    expect(taskTimeBucket(at(2026, 1, 1), now)).toBe("earlier");
  });

  it("treats unparsable timestamps as 更早 instead of throwing", () => {
    expect(taskTimeBucket("not-a-date", now)).toBe("earlier");
  });
});
