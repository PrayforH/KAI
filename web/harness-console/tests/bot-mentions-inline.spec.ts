import { describe, expect, it } from "vitest";
import { parseMentions, removeMentionTokens } from "../src/lib/bot-mentions";

const ASSISTANTS = [
  { specialistId: "specialist_a", name: "资料 Bot", description: "盯行业信号" },
  { specialistId: "specialist_b", name: "研究 Bot", description: "读论文" },
  { specialistId: "specialist_c", name: "研究 Bot2", description: "读论文的兄弟" },
];

describe("行内 @ 提及（和普通聊天一样写在文本行里，可以多个）", () => {
  it("parses one mention from the middle of a sentence", () => {
    const parsed = parseMentions("请 @研究 Bot 帮我看看这篇", ASSISTANTS);
    expect(parsed.map((bot) => bot.specialistId)).toEqual(["specialist_b"]);
  });

  it("parses several mentions in one line without duplicates", () => {
    const parsed = parseMentions("@资料 Bot 盯盘，@研究 Bot 读论文，@资料 Bot 别漏", ASSISTANTS);
    expect(parsed.map((bot) => bot.specialistId)).toEqual(["specialist_a", "specialist_b"]);
  });

  it("matches the longest name so @研究Bot2 is not 研究Bot", () => {
    const parsed = parseMentions("@研究 Bot2 也在吗", ASSISTANTS);
    expect(parsed.map((bot) => bot.specialistId)).toEqual(["specialist_c"]);
  });

  it("drops a mention when its text is deleted", () => {
    const parsed = parseMentions("请 帮我看看这篇", ASSISTANTS);
    expect(parsed).toEqual([]);
  });

  it("ignores text without any @", () => {
    expect(parseMentions("普通消息没有提及", ASSISTANTS)).toEqual([]);
  });

  it("removes delivered tokens and keeps the rest for retry", () => {
    const left = removeMentionTokens(
      "@资料 Bot 盯盘，@研究 Bot 读论文",
      [ASSISTANTS[0]],
    );
    expect(left).toBe("盯盘，@研究 Bot 读论文");
  });
});
