import { expect, it } from "vitest";
import { responseTextForTurn, turnDeliverablesReady, shouldSuppressNativeAssistantText } from "../src/components/agent-thread";
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

it("holds deliverables through model completion and until both run and message settle", () => {
  expect(turnDeliverablesReady("running", "completed")).toBe(false); // previous run snapshot
  expect(turnDeliverablesReady("complete", "running")).toBe(false); // model done, publishing
  expect(turnDeliverablesReady("complete", "completed")).toBe(true);
  expect(turnDeliverablesReady("incomplete", "failed")).toBe(true); // completed partial files
  expect(turnDeliverablesReady("complete")).toBe(true); // historical earlier turn
});
it("does not paint a recycled native answer while awaiting this turn's first server message", () => {
  const captured = {current: {messageId: "assistant-first", text: "旧答案"}};
  const next: LiveResponseSnapshot = {runId: "second", text: "", status: "streaming", visible: false};
  const suppressed = shouldSuppressNativeAssistantText(false, next, true);
  expect(suppressed).toBe(true);
  expect(responseTextForTurn("__optimistic__second", "旧答案", suppressed, next, captured)).toBe("");
  expect(responseTextForTurn("assistant-second", "旧答案", true, {...next, visible:true, text:"新答案"}, captured)).toBe("新答案");
});
