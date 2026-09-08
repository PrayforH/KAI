import { describe, expect, it } from "vitest";
import {
  ACTIVE_TASK_REFRESH_MS,
  ERROR_TASK_REFRESH_MS,
  IDLE_TASK_REFRESH_MS,
  taskListRefreshDelay,
} from "../src/lib/task-list-refresh";

describe("task list refresh policy", () => {
  it("keeps active task and approval updates responsive", () => {
    expect(taskListRefreshDelay(["succeeded", "running"])).toBe(
      ACTIVE_TASK_REFRESH_MS,
    );
    expect(taskListRefreshDelay(["succeeded"], "waiting_approval")).toBe(
      ACTIVE_TASK_REFRESH_MS,
    );
  });

  it("backs off when the workspace is idle", () => {
    expect(taskListRefreshDelay(["succeeded", "failed"], "completed")).toBe(
      IDLE_TASK_REFRESH_MS,
    );
    expect(taskListRefreshDelay([], undefined)).toBe(IDLE_TASK_REFRESH_MS);
  });

  it("retries transient list failures without returning to a hot loop", () => {
    expect(taskListRefreshDelay(["running"], "running", true)).toBe(
      ERROR_TASK_REFRESH_MS,
    );
  });
});
