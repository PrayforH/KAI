import { describe, expect, it } from "vitest";
import { assistantChatReplies, chatTimeLabel, showChatTime } from "../src/lib/assistant-chat";
import type { RunActivity } from "../src/lib/activity-schema";
const activity = (events: [string, string?, string?][], status = "running"): RunActivity => ({
  run_id: "r", status, started_at: "2026-11-02T00:00:00Z", metrics: {},
  items: events.map(([event_type, summary, message_id], sequence) => ({id: `e${sequence}`, event_type,
    summary, metadata: { message_id }, sequence, kind: "analysis", status: "succeeded", title: "", timestamp: `2026-11-02T00:00:${String(sequence).padStart(2, "0")}Z` })),
});
describe("Bot public message chronology", () => {
  it("shows the first real delta immediately, before completion or any timer", () => {
    expect(assistantChatReplies(activity([["message.delta", "我先核对", "m"]]))).toEqual([
      {id: "e0", text: "我先核对", timestamp: "2026-11-02T00:00:00Z", streaming: true},
    ]);
  });
  it("keeps pre-tool messages and streams the next one even with a reused ID", () => {
    const a = activity([["message.delta", "先核对。", "m"], ["tool.request"], ["tool.result"], ["message.delta", "核对完了。", "m"]]);
    expect(assistantChatReplies(a).map(x => [x.text, x.streaming])).toEqual([["先核对。", false], ["核对完了。", true]]);
    expect(assistantChatReplies({...a, status: "succeeded"}).map(x => [x.text, x.streaming])).toEqual([["先核对。", false], ["核对完了。", false]]);
  });
  it("splits actual message boundaries but does not turn paragraphs or tokens into messages", () => {
    const a = activity([["message.start", undefined, "a"], ["message.delta", "第一段\n\n第二段", "a"], ["message.delta", "补充", "a"],
      ["message.completed", undefined, "a"], ["message.start", undefined, "b"], ["message.delta", "另一条消息", "b"]]);
    expect(assistantChatReplies(a).map(x => x.text)).toEqual(["第一段\n\n第二段补充", "另一条消息"]);
  });
  it("never publishes private reasoning, arguments, tool results or synthetic acknowledgements", () => {
    expect(assistantChatReplies(activity([["reasoning.delta", "private"], ["tool.request", "secret"], ["tool.result", "private-file"], ["runtime.system", "等待"]]))).toEqual([]);
  });
  it("retains partial messages after failure, and restores exactly the same messages from history", () => {
    const a = activity([["message.delta", "已经核对第一项。", "a"], ["tool.request"], ["run.failed"]], "failed");
    expect(assistantChatReplies(JSON.parse(JSON.stringify(a)))).toEqual(assistantChatReplies(a));
    expect(assistantChatReplies(a)).toHaveLength(1);
    expect(assistantChatReplies(a)[0].streaming).toBe(false);
  });
});
describe("honest chat timestamps", () => {
  it("separates five-minute gaps, dates and years without repeating every message", () => {
    expect(showChatTime("2026-11-02T08:00:00+08:00")).toBe(true);
    expect(showChatTime("2026-11-02T08:04:59+08:00", "2026-11-02T08:00:00+08:00")).toBe(false);
    expect(showChatTime("2026-11-02T08:05:00+08:00", "2026-11-02T08:00:00+08:00")).toBe(true);
    expect(showChatTime("invalid")).toBe(false); expect(showChatTime(undefined)).toBe(false);
    const now = new Date(2026, 10, 2, 12);
    expect(chatTimeLabel(new Date(2026, 10, 2, 8, 18).toISOString(), now)).toBe("今天 8:18");
    expect(chatTimeLabel(new Date(2026, 10, 1, 8, 48).toISOString(), now)).toBe("昨天 8:48");
    expect(chatTimeLabel(new Date(2026, 9, 31, 14, 10).toISOString(), now)).toBe("10月31日周六 14:10");
    expect(chatTimeLabel("invalid", now)).toBeNull();
  });
});

import { publicChatText } from "../src/lib/assistant-chat";
it("keeps reserved form payloads out of streamed chat without hiding ordinary code", () => {
  expect(publicChatText('先确认范围。\n```harness-input\n{"version":1,')).toBe('先确认范围。');
  expect(publicChatText('结果如下。\n```harness-ui\n{"schema_version":')).toBe('结果如下。');
  expect(publicChatText('```python\nprint(1)\n```')).toBe('```python\nprint(1)\n```');
});


it("does not stamp fresh sends, even after an old history gap", () => {
  const now = Date.parse("2026-11-02T18:51:00+08:00");
  const previous = "2026-11-02T17:00:00+08:00";
  expect(showChatTime("2026-11-02T18:51:00+08:00", previous, now)).toBe(false);
  expect(showChatTime("2026-11-02T18:51:00+08:00", undefined, now)).toBe(false);
  expect(showChatTime("2026-11-02T18:46:01+08:00", previous, now)).toBe(false);
  expect(showChatTime("2026-11-02T18:46:00+08:00", previous, now)).toBe(true);
  expect(showChatTime("2026-11-02T18:46:00+08:00", "2026-11-02T18:45:00+08:00", now)).toBe(false);
});
