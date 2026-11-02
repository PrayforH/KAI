import { expect, it } from "vitest";
import { noticeExchangeForMessage } from "../src/components/assistant-notice-source";
const notice = (id: string, botId: string, timestamp?: string) => ({id, content: [{type: "tool-call", toolName: "harness_notice_source",
  args: { name: `Bot ${botId}`, botId, href: `/assistant?bot=${botId}`, timestamp }}]});
it("counts actual adjacent Bot messages and keeps each distinct source clickable", () => {
  const messages = [notice("n1", "a", "2026-11-02T08:00:00+08:00"), notice("n2", "b", "2026-11-02T08:01:00+08:00"), notice("n3", "a", "2026-11-02T08:02:00+08:00")];
  expect(noticeExchangeForMessage(messages, "n1")).toMatchObject({hidden: false, count: 3, sources: [{botId: "a"}, {botId: "b"}]});
  expect(noticeExchangeForMessage(messages, "n2")?.hidden).toBe(true);
});
it("does not merge over a user turn, a time gap, or an unknown historical timestamp", () => {
  const first = notice("n1", "a", "2026-11-02T08:00:00+08:00");
  expect(noticeExchangeForMessage([first, {id: "user", content: []}, notice("n2", "b", "2026-11-02T08:01:00+08:00")], "n1")?.count).toBe(1);
  expect(noticeExchangeForMessage([first, notice("n2", "b", "2026-11-02T08:05:00+08:00")], "n1")?.count).toBe(1);
  expect(noticeExchangeForMessage([notice("old", "a"), first], "old")).toBeNull();
});
