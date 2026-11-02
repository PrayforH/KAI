import { describe, expect, it } from "vitest";
import { composerOptions } from "../src/components/composer-assist";
import { MENTION_PREFIX } from "../src/lib/bot-mentions";

const ASSISTANTS = [
  { specialistId: "specialist_a", name: "资料 Bot", description: "盯行业信号" },
  { specialistId: "specialist_b", name: "研究 Bot", description: "读论文" },
];

describe("@ 提及助理", () => {
  it("lists accessible assistants first with a stable id", () => {
    const options = composerOptions("@", 1, ASSISTANTS);
    expect(options[0]?.id).toBe(`${MENTION_PREFIX}specialist_a`);
    expect(options[0]?.label).toBe("@资料 Bot");
    expect(options[1]?.id).toBe(`${MENTION_PREFIX}specialist_b`);
  });

  it("filters by name", () => {
    const options = composerOptions("@研究", 3, ASSISTANTS);
    expect(options.map((option) => option.id)).toEqual([`${MENTION_PREFIX}specialist_b`]);
  });

  it("keeps knowledge bases after the assistants and still multi-selects them", () => {
    const bases = [{ reference: "kb-1", displayName: "手册库", kbType: "文件" }];
    const options = composerOptions("@", 1, ASSISTANTS, [], bases, []);
    expect(options.map((option) => option.id)).toEqual([
      `${MENTION_PREFIX}specialist_a`,
      `${MENTION_PREFIX}specialist_b`,
      "@kb-1",
    ]);
    expect(options[2]?.description).toContain("可多选");
  });

  it("offers no assistant when the surface is not the assistant one", () => {
    const options = composerOptions("@", 1, [], [], [{ reference: "kb-1", displayName: "手册库", kbType: "文件" }], []);
    expect(options.map((option) => option.id)).toEqual(["@kb-1"]);
  });
});
