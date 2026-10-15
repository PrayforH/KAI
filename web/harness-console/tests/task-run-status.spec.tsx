// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it } from "vitest";
import { TaskRunStatus } from "../src/components/task-run-status";
import { taskRunNotice } from "../src/lib/task-run-notice";
import { runActivitySchema } from "../src/lib/activity-schema";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
const failed = runActivitySchema.parse({run_id: "old", status: "failed", started_at: "2026-10-12T00:00:00Z", items: [{id: "e", event_type: "run.failed", kind: "error", status: "failed", title: "运行失败", sequence: 1, timestamp: "2026-10-12T00:00:01Z", metadata: {error_type: "CLIJSONDecodeError"}}]});
it("shows legacy parser failures with an actionable reason in the header", () => {
  const host = document.createElement("div"); const root = createRoot(host);
  act(() => root.render(<TaskRunStatus activity={failed} stream={{status: "error", runId: "old"}} />));
  expect(host.querySelector('[role="status"]')?.textContent).toBe("运行失败");
  expect(host.querySelector("details p")?.textContent).toContain("运行消息过大或格式异常");
  expect(host.querySelector("summary")?.getAttribute("aria-label")).toContain("查看原因");
  act(() => root.unmount());
});
it("does not label a disconnected running task as failed", () => {
  expect(taskRunNotice(undefined, {status: "error"}, "running")?.label).toBe("连接中断");
});
it("a new run replaces an old failure and its detail", () => {
  const notice = taskRunNotice(failed, {status: "running", runId: "new"}, "failed");
  expect(notice?.label).toBe("正在运行"); expect(notice?.detail).toBeNull();
});
it("normal completion does not retain a previous failure", () => {
  const completed = {...failed, status: "succeeded", items: []};
  expect(taskRunNotice(completed, {status: "complete", runId: "old"})?.label).toBe("已完成");
});
it("a new task has no fabricated status", () => {
  expect(taskRunNotice(undefined, {status: "idle"})).toBeNull();
});
