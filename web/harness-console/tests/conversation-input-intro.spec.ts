import { expect, it } from "vitest";
import { conversationInputDisplay, conversationInputIntro, parseConversationInput } from "../src/lib/conversation-input";
const block = '```harness-input\n'+JSON.stringify({ version: 1, title: "确认验收范围", questions: [{ id: "scope", label: "这次验收选择哪个范围？", type: "single", options: ["仅网页", "网页与测试", "暂不执行"] }] })+'\n```';
it("shows a question once when its interactive card is mounted, preserving surrounding prose and results", () => {
  const text = "请选择本次验收的范围：\n\n"+block+"\n\n|文件|状态|\n|---|---|\n|报告|就绪|";
  const intro = conversationInputIntro(text);
  expect(intro).toContain("请选择本次验收的范围");
  expect(intro).toContain("|报告|就绪|");
  expect(intro).not.toContain("这次验收选择哪个范围");
  expect(intro).not.toContain("仅网页");
  expect(parseConversationInput(text)?.questions[0].options).toHaveLength(3);
  // No mounted card (including history after an answer) retains a readable transcript.
  expect(conversationInputDisplay(text)).toContain("这次验收选择哪个范围？（仅网页 / 网页与测试 / 暂不执行）");
});
it("does not leave an empty duplicate bubble for a protocol-only question", () => {
  expect(conversationInputIntro(block)).toBe("");
});
it("keeps incomplete, ambiguous and ordinary quoted examples intact", () => {
  for (const text of ["普通回答", block.slice(0, -40), block+"\n\n"+block, '```typescript\n'+block+'\n```']) {
    if (!parseConversationInput(text)) expect(conversationInputIntro(text)).toBe(text);
  }
});
