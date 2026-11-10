import { describe, expect, it } from "vitest";

import {
  WEEKDAYS_CRON,
  buildCron,
  buildDutyCron,
  describeCron,
  nextWeekdayEvenHour,
  readCronFrequency,
  readDutyRepeat,
  scheduleSummary,
} from "../src/lib/automation-schedule";
import type { ApiAutomationTask } from "../src/lib/studio-client";

function taskWithCron(cron: string): ApiAutomationTask {
  return {
    schedule: { type: "cron", cron, timezone: "Asia/Shanghai" },
  } as unknown as ApiAutomationTask;
}

describe("reading a cron back into the form", () => {
  it("recognises the frequencies the form itself produces", () => {
    expect(readCronFrequency(buildCron("daily", "08:30", 5), 5)).toEqual({
      frequency: "daily",
      time: "08:30",
      weekday: 5,
    });
    expect(readCronFrequency(buildCron("weekly", "18:00", 5), 1)).toEqual({
      frequency: "weekly",
      time: "18:00",
      weekday: 5,
    });
    expect(readCronFrequency(WEEKDAYS_CRON, 1)).toEqual({ frequency: "weekdays" });
  });

  it("leaves a schedule the form cannot express untouched", () => {
    // Every four hours: previously read as "weekdays", so opening a task and saving
    // it silently changed when the agent ran.
    expect(readCronFrequency("0 */4 * * *", 5)).toBeNull();
    // Monthly on the 15th: previously read as "daily at 09:00".
    expect(readCronFrequency("0 9 15 * *", 5)).toBeNull();
    expect(readCronFrequency("30 22 1 3 *", 5)).toBeNull();
    expect(readCronFrequency("not a cron", 5)).toBeNull();
    // A null reading is what keeps the form on `custom`, so the expression it holds
    // is the one that gets saved back.
    expect(readCronFrequency("0 */4 * * *", 5) ?? { frequency: "custom" }).toEqual({
      frequency: "custom",
    });
  });
});

describe("summarising a schedule", () => {
  it("does not describe every interval as a workday schedule", () => {
    expect(scheduleSummary(taskWithCron("0 */2 * * 1-5"))).toBe("工作日每 2 小时");
    expect(scheduleSummary(taskWithCron("0 */4 * * *"))).toBe("每天每 4 小时");
    expect(scheduleSummary(taskWithCron("0 */4 * * 1-5"))).toBe("工作日每 4 小时");
  });

  it("names monthly and annual clocks instead of dumping the expression", () => {
    expect(scheduleSummary(taskWithCron("0 9 15 * *"))).toBe("每月 15 日 09:00");
    expect(scheduleSummary(taskWithCron("0 9 1 3 *"))).toBe("每年 3 月 1 日 09:00");
    expect(scheduleSummary(taskWithCron("0 9 * * *"))).toBe("每天 09:00");
    expect(scheduleSummary(taskWithCron("0 18 * * 5"))).toBe("每周五 18:00");
  });

  it("falls back to the expression instead of guessing", () => {
    expect(scheduleSummary(taskWithCron("0 9 * * 1-5"))).toBe("0 9 * * 1-5");
    expect(scheduleSummary(taskWithCron("30 22 1 3 1"))).toBe("30 22 1 3 1");
  });
});

describe("the duty repeat vocabulary", () => {
  it("round-trips every repeat the editor offers", () => {
    const repeats = [
      { repeat: "daily", time: "09:00" },
      { repeat: "weekly", time: "18:30", weekday: 5 },
      { repeat: "monthly", time: "08:00", dayOfMonth: 15 },
      { repeat: "yearly", time: "07:05", dayOfMonth: 1, month: 3 },
    ] as const;
    for (const repeat of repeats) {
      const cron = buildDutyCron(repeat);
      expect(readDutyRepeat(cron)).toEqual(repeat);
      expect(describeCron(cron)).not.toBeNull();
    }
    expect(buildDutyCron(repeats[0])).toBe("0 9 * * *");
    expect(buildDutyCron(repeats[1])).toBe("30 18 * * 5");
    expect(buildDutyCron(repeats[2])).toBe("0 8 15 * *");
    expect(buildDutyCron(repeats[3])).toBe("5 7 1 3 *");
  });

  it("keeps expressions it cannot describe on the raw cron", () => {
    expect(readDutyRepeat("0 9 * * 1-5")).toBeNull();
    expect(readDutyRepeat("*/15 * * * *")).toBeNull();
    expect(describeCron("30 22 1 3 1")).toBeNull();
    expect(describeCron("0 9 32 * *")).toBeNull();
    expect(readDutyRepeat("not a cron")).toBeNull();
  });

  it("accepts 7 as Sunday, like the cron parser does", () => {
    expect(readDutyRepeat("0 9 * * 7")).toEqual({ repeat: "weekly", time: "09:00", weekday: 0 });
    expect(describeCron("0 9 * * 7")).toBe("每周日 09:00");
  });
});

describe("the next run of the weekday template", () => {
  it("only lands on a weekday, at an even hour, in the future", () => {
    // Friday 22:30 — the next firing is Saturday? No: the cron skips the weekend.
    const fridayLate = new Date("2026-09-25T22:30:00");
    const next = nextWeekdayEvenHour(fridayLate);
    expect([1, 2, 3, 4, 5]).toContain(next.getDay());
    expect(next.getHours() % 2).toBe(0);
    expect(next.getTime()).toBe(new Date("2026-09-28T00:00:00").getTime());

    const mondayMorning = new Date("2026-09-21T09:15:00");
    expect(nextWeekdayEvenHour(mondayMorning).getTime()).toBe(
      new Date("2026-09-21T10:00:00").getTime(),
    );
  });
});
