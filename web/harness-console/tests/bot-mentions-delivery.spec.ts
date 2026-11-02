import { describe, expect, it } from "vitest";
import { deliverMentions } from "../src/lib/bot-mentions";

const MENTIONS = [
  { specialistId: "specialist_a", name: "资料 Bot" },
  { specialistId: "specialist_b", name: "研究 Bot" },
];

describe("mention delivery", () => {
  it("delivers to every target when all succeed", async () => {
    const seen: string[] = [];
    const result = await deliverMentions(MENTIONS, "看一下", async (bot) => {
      seen.push(bot.specialistId);
    });
    expect(seen).toEqual(["specialist_a", "specialist_b"]);
    expect(result.delivered).toEqual(["specialist_a", "specialist_b"]);
    expect(result.failed).toEqual([]);
  });

  it("keeps going when one target fails", async () => {
    const result = await deliverMentions(MENTIONS, "看一下", async (bot) => {
      if (bot.specialistId === "specialist_a") throw new Error("资料 Bot 已暂停接单");
    });
    // 失败的目标不阻断其余目标：研究 Bot 照样收到。
    expect(result.delivered).toEqual(["specialist_b"]);
    expect(result.failed).toEqual([
      { specialistId: "specialist_a", name: "资料 Bot", reason: "资料 Bot 已暂停接单" },
    ]);
  });

  it("reports every failure when nothing could be delivered", async () => {
    const result = await deliverMentions(MENTIONS, "看一下", async () => {
      throw new Error("这个 Bot 已归档，收不到新消息");
    });
    expect(result.delivered).toEqual([]);
    expect(result.failed.map((entry) => entry.specialistId)).toEqual([
      "specialist_a",
      "specialist_b",
    ]);
  });
});
