import { describe, expect, it } from "vitest";
import { composerOptions } from "../src/components/composer-assist";
import { MENTION_PREFIX } from "../src/lib/bot-mentions";

const ASSISTANTS = [
  { specialistId: "specialist_a", name: "资料 Bot", description: "盯行业信号", appearance: { color: "#d2550a", avatar: "blob" } },
  { specialistId: "specialist_b", name: "研究 Bot", description: "读论文" },
];

describe("@ 提及助理", () => {
  it("lists accessible assistants first with a stable id", () => {
    const options = composerOptions("@", 1, ASSISTANTS);
    expect(options[0]?.id).toBe(`${MENTION_PREFIX}specialist_a`);
    expect(options[0]).toMatchObject({ label: "资料 Bot", bot: { appearance: ASSISTANTS[0].appearance } });
    expect(options[1]?.id).toBe(`${MENTION_PREFIX}specialist_b`);
  });

  it("filters by name", () => {
    const options = composerOptions("@研究", 3, ASSISTANTS);
    expect(options.map((option) => option.id)).toEqual([`${MENTION_PREFIX}specialist_b`]);
  });

  it("only offers registered assistant candidates for @", () => {
    expect(composerOptions("@", 1)).toEqual([]);
    expect(composerOptions("@手册", 3, ASSISTANTS)).toEqual([]);
  });
});
