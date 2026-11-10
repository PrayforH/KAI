import { describe, expect, it } from "vitest";
import { buildCatalog } from "../src/components/agent-studio/skills-catalog-page";
import type { PersonalSkill } from "../src/lib/assistant-client";

const personal: PersonalSkill = {
  skillId: "pskill_1",
  name: "project-risk-weekly-report",
  displayName: "项目风险周报",
  description: "汇总项目风险并生成周报",
  enabled: true,
  revision: 4,
  sourceProposalId: "proposal_1",
  skill: {
    name: "project-risk-weekly-report",
    description: "汇总项目风险并生成周报",
    instructions: "先校验输入，再生成进度表、风险清单和周报。",
    files: [{ path: "references/rules.md" }],
  },
  createdAt: "2026-11-10T00:00:00Z",
  updatedAt: "2026-11-10T00:00:00Z",
};

describe("personal Skills in the catalog", () => {
  it("appears under the personal scope with its enabled state", () => {
    const catalog = buildCatalog([], [], [personal]);
    expect(catalog).toHaveLength(1);
    const [row] = catalog;
    expect(row.key).toBe("personal-skill:pskill_1");
    expect(row.scope).toBe("personal");
    expect(row.displayName).toBe("项目风险周报");
    expect(row.instructions).toContain("先校验输入");
    expect(row.fileCount).toBe(1);
    expect(row.personalSkill?.enabled).toBe(true);
    expect(row.personalSkill?.revision).toBe(4);
  });

  it("renders a disabled Skill without pretending it is active", () => {
    const catalog = buildCatalog([], [], [{ ...personal, enabled: false }]);
    expect(catalog[0].personalSkill?.enabled).toBe(false);
  });
});
