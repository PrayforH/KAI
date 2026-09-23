import { describe, expect, it } from "vitest";
import { parseConversationInput, conversationInputDisplay, latestConversationInput, formatConversationAnswers } from "../src/lib/conversation-input";
const payload = {version: 1, title: "确认范围", questions: [{id: "scope", label: "选择内容", type: "multi", options: ["UI", "接口"]}, {id: "note", label: "补充要求", type: "text"}]};
const fence = (data: unknown) => "```harness-input\n" + JSON.stringify(data) + "\n```";
describe("model-driven input display protocol", () => {
  it("accepts bounded choices and free text, emits ordinary user answers", () => {
    const input = parseConversationInput(fence(payload))!;
    expect(input.questions).toHaveLength(2);
    expect(formatConversationAnswers(input, {scope: {selected: ["UI", "接口"], text: ""}})).toBeNull();
    expect(formatConversationAnswers(input, {scope: {selected: ["UI", "接口", "injected"], text: ""}, note: {selected: [], text: "保留布局"}})).toBe("确认范围\n\n选择内容\nUI；接口\n\n补充要求\n保留布局");
    expect(formatConversationAnswers(input, {scope: {selected: [], text: "只修改文案"}, note: {selected: [], text: "保留布局"}})).toContain("只修改文案");
  });
  it("rejects malformed, duplicate, oversized and non-final example blocks", () => {
    for (const data of [null, {}, {...payload, version: 2}, {...payload, questions: Array(5).fill(payload.questions[0])}, {...payload, questions: [payload.questions[0], payload.questions[0]]}, {...payload, questions: [{...payload.questions[0], options: ["same", "same"]}]}, {...payload, title: "x".repeat(161)}, {...payload, questions: [{...payload.questions[0], type: "execute"}]}]) expect(parseConversationInput(fence(data))).toBeNull();
    expect(parseConversationInput(fence(payload) + "\nexample only")).toBeNull();
    expect(parseConversationInput(fence(payload) + "\n" + fence(payload))).toBeNull();
  });
  it("does not re-offer older, answered or unfinished assistant requests", () => {
    const message = {id: "a", role: "assistant", status: {type: "complete"}, content: [{type: "text", text: fence(payload)}]};
    expect(latestConversationInput([message])?.messageId).toBe("a");
    expect(latestConversationInput([message, {id: "u", role: "user", content: [{type: "text", text: "answer"}]}])).toBeNull();
    expect(latestConversationInput([{...message, status: {type: "running"}}])).toBeNull();
    expect(latestConversationInput([{...message, status: {type: "incomplete"}}])).toBeNull();
    expect(latestConversationInput([])).toBeNull();
  });
  it("keeps readable history and hides partial JSON only while streaming", () => {
    expect(conversationInputDisplay("请确认\n" + fence(payload))).toContain("选择内容（UI / 接口）");
    expect(conversationInputDisplay(fence(payload))).not.toContain("harness-input");
    const partial = '正在整理\n```harness-input\n{"version":';
    expect(conversationInputDisplay(partial, true)).toBe("正在整理");
    expect(conversationInputDisplay(partial)).toBe(partial);
  });
});
