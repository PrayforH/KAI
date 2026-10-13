import { expect, it } from "vitest";
import { responseTextForTurn } from "../src/components/agent-thread";
import type { LiveResponseSnapshot } from "../src/lib/live-response-store";
const idle: LiveResponseSnapshot = {text: "", status: "idle", visible: false};
it("keeps the cleaned answer when the next turn starts before history commits", () => {
  const captured = {current: undefined as {messageId: string; text: string} | undefined};
  const native = "我先读取文件。\n第一轮最终答案";
  const live: LiveResponseSnapshot = {messageId: "assistant-first", text: "第一轮最终答案", status: "complete", visible: true};
  expect(responseTextForTurn("assistant-first", native, true, live, captured)).toBe("第一轮最终答案");
  const next: LiveResponseSnapshot = {runId: "second", text: "", status: "streaming", visible: false};
  // The previous message is no longer last and the raw stream still has its preface.
  expect(responseTextForTurn("assistant-first", native, false, next, captured)).toBe("第一轮最终答案");
  expect(responseTextForTurn("assistant-first", "第一轮最终答案", false, next, captured)).toBe("第一轮最终答案");
});
it("keeps hidden progress hidden, then accepts the durable response at hand-off", () => {
  const captured = {current: undefined as {messageId: string; text: string} | undefined};
  const hidden: LiveResponseSnapshot = {text: "正在读取文件", visible: false, status: "streaming"};
  expect(responseTextForTurn("assistant-first", hidden.text, true, hidden, captured)).toBe("");
  expect(responseTextForTurn("assistant-first", hidden.text, false, {...hidden, text: ""}, captured)).toBe("");
  expect(responseTextForTurn("assistant-first", "持久化最终答案", false, idle, captured)).toBe("持久化最终答案");
});
it("never carries a cached response into a recycled message slot", () => {
  const captured = {current: {messageId: "assistant-first", text: "旧答案"}};
  expect(responseTextForTurn("assistant-second", "新答案", false, {...idle, status: "streaming"}, captured)).toBe("新答案");
});
