// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { activitySteps, modelWaitingSince, progressPhrase, shouldShowChatActivityRow, type ActivityLike } from "../src/lib/assistant-progress";
import { AssistantProgress } from "../src/components/assistant-progress";
import { GrokActivityRow } from "../src/components/grok-activity";
import { runActivitySchema } from "../src/lib/activity-schema";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
const note = "已经确认原任务仍在运行，接下来核对它生成的文件。";
const request = (id: string, name: string): ActivityLike => ({id, event_type: "tool.request", metadata: {tool_call_id: id, name, arguments: {token: "PRIVATE_TOOL_ARGUMENT"}}});
const result = (id: string, status = "succeeded"): ActivityLike => ({event_type: "tool.result", status, metadata: {tool_call_id: id, result_preview: "PRIVATE_TOOL_RESULT", result_summary: "返回 12 行"}});

it("keeps long public intermediate summaries, excluding final prose and private reasoning", () => {
  const items = [
    {event_type: "reasoning.delta", summary: "PRIVATE_REASONING"},
    {event_type: "message.delta", summary: note.repeat(10)},
    request("t1", "Read"), result("t1"),
    {event_type: "message.delta", summary: "FINAL_ANSWER_ONLY"},
  ];
  const steps = activitySteps({items});
  expect(steps[0].text).toContain("已经确认原任务仍在运行");
  expect(steps[0].text.length).toBeGreaterThan(10);
  expect(steps[0].text.length).toBeLessThanOrEqual(96);
  expect(JSON.stringify(steps)).not.toMatch(/PRIVATE_|FINAL_ANSWER/);
});

it("accepts the explicit public summary channel before the first tool", () => {
  expect(progressPhrase({items: [{event_type: "reasoning.summary.delta", summary: note}]})).toBe(note);
  expect(progressPhrase({items: [{event_type: "message.delta", summary: "最终答案"}]})).toBe("");
});

it.each([
  ["WebSearch", "正在检索相关资料。"], ["Read", "正在读取所需文件。"],
  ["Bash", "正在执行所需命令。"], ["mcp__computer__screenshot", "正在操作云电脑。"],
  ["mcp__harness-builder__assistant_delegate_to_specialist", "正在联系子助理。"],
  ["mcp__harness-builder__assistant_task_status", "正在核对子助理和原任务。"],
])("uses actual pending operations instead of a fixed wait (%s)", (name, expected) => {
  expect(progressPhrase({items: [request("t1", name)]})).toBe(expected);
});

it("does not mistake one parallel result or an authorization for all operations completing", () => {
  const items = [request("read", "Read"), request("search", "WebSearch"), result("search")];
  expect(progressPhrase({items})).toBe("正在读取所需文件。");
  expect(progressPhrase({items: [...items, {event_type: "tool.allowed", metadata: {tool_call_id: "read"}}]})).toBe("正在读取所需文件。");
  expect(progressPhrase({items: [...items, result("read")]})).toBe("文件内容已返回，正在整理。");
});

it("distinguishes the delivered handover receipt from a completed task", () => {
  const text = progressPhrase({items: [request("h", "assistant_delegate_to_specialist"), result("h")]});
  expect(text).toBe("已收到交办回执，正在核对。");
  expect(text).not.toContain("已完成");
});

it("keeps pending consent and real failures ahead of older summaries", () => {
  const items = [{event_type: "reasoning.summary.delta", summary: note}, request("t", "Bash")];
  expect(progressPhrase({items: [...items, {event_type: "approval.requested", metadata: {approval_id: "a"}}]})).toBe("需要你确认后才能继续。");
  expect(progressPhrase({items: [...items, {event_type: "tool.result", status: "failed", summary: "检索超时", metadata: {tool_call_id: "t"}}]})).toContain("检索超时");
  expect(progressPhrase({items: [...items, {event_type: "approval.rejected"}]})).toContain("未获允许");
});

it("keeps at most three recent distinct summaries and no duplicate main phrase", () => {
  const items = Array.from({length: 8}, (_, i) => ({event_type: "reasoning.summary.delta", summary: `已核对第 ${i} 项信息。`}));
  expect(activitySteps({items}, {excludeText: "已核对第 7 项信息。"}).map(step => step.text)).toEqual(["已核对第 4 项信息。", "已核对第 5 项信息。", "已核对第 6 项信息。"]);
});

it("does not leave progress in completed history", () => {
  for (const status of ["completed", "succeeded", "failed", "cancelled", "timed_out"]) {
    expect(shouldShowChatActivityRow(status, {items: [request("t", "Read")]})).toBe(false);
  }
});

let cleanup: (() => Promise<void>) | undefined;
afterEach(async () => { await cleanup?.(); cleanup = undefined; vi.useRealTimers(); });
it("shows a full updated summary immediately without replaying a synthetic typing delay", async () => {
  const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
  cleanup = async () => { await act(async () => root.unmount()); host.remove(); };
  await act(async () => root.render(<GrokActivityRow phrase="正在处理你的请求。" state="working" />));
  await act(async () => root.render(<GrokActivityRow phrase={note} state="working" />));
  expect(host.textContent).toBe(note);
});

it("reports real elapsed model waiting, then clears it on a new operation or completion", async () => {
  vi.useFakeTimers(); vi.setSystemTime(new Date("2026-11-02T10:00:00Z"));
  const host = document.createElement("div"); document.body.append(host); const root = createRoot(host);
  cleanup = async () => { await act(async () => root.unmount()); host.remove(); };
  const activity = runActivitySchema.parse({run_id: "progress-run", status: "running", started_at: "2026-11-02T10:00:00Z", items: [{id: "wait", event_type: "runtime.system", title: "模型正在处理", kind: "analysis", status: "running", timestamp: "2026-11-02T10:00:00Z", sequence: 1, summary: "正在等待本轮模型结果", metadata: {}}]});
  await act(async () => root.render(<AssistantProgress activity={activity} />));
  expect(host.textContent).toBe("正在处理你的请求。");
  await act(async () => vi.advanceTimersByTime(31000));
  expect(host.textContent).toContain("已等待 31 秒");
  const toolActivity = runActivitySchema.parse({...activity, items: [...activity.items, {...activity.items[0], ...request("read", "Read"), sequence: 2}]});
  expect(modelWaitingSince(toolActivity)).toBeUndefined();
  await act(async () => root.render(<AssistantProgress activity={toolActivity} />));
  expect(host.textContent).toBe("正在读取所需文件。");
  await act(async () => root.render(<AssistantProgress activity={{...toolActivity, status: "succeeded"}} />));
  expect(host.textContent).toBe("");
});
