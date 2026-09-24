import { describe, expect, it } from "vitest";
import { matchesPlatformSkillQuery, matchesSkillQuery } from "../src/lib/skill-catalog-search";

const ppt = {
  name: "pptx-generator",
  displayName: "PowerPoint 演示（MiniMax）",
  description: "创建 PPTX 演示文稿",
  agents: [],
  package: {
    packageId: "pptx-generator",
    sourceUrl: "https://github.com/MiniMax-AI/skills/tree/main/skills/pptx-generator",
    tags: ["办公", "PPT", "pptx"],
  },
};

const companion = {
  ...ppt,
  name: "slide-making-skill",
  displayName: "幻灯片制作",
  package: {
    packageId: "slide-making-skill",
    sourceUrl: "https://github.com/MiniMax-AI/skills/tree/main/plugins/pptx-plugin/skills/slide-making-skill",
    tags: ["办公", "PPT", "制作"],
  },
};

describe("skill catalog search", () => {
  it("matches separate words across brand, name and tags", () => {
    expect(matchesSkillQuery(ppt, "MiniMax PPT")).toBe(true);
    expect(matchesSkillQuery(companion, "minimax ppt")).toBe(true);
    expect(matchesPlatformSkillQuery({ ...ppt.package, displayName: ppt.displayName, summary: ppt.description }, "MiniMax PPT")).toBe(true);
    expect(matchesPlatformSkillQuery({ ...companion.package, displayName: companion.displayName, summary: companion.description }, "MiniMax PPT")).toBe(true);
    expect(matchesSkillQuery(ppt, "pptx-generator")).toBe(true);
  });

  it("requires every word while preserving an empty search", () => {
    expect(matchesSkillQuery(ppt, "MiniMax PDF")).toBe(false);
    expect(matchesSkillQuery(ppt, "  ")).toBe(true);
  });
});
