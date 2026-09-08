import { describe, expect, it } from "vitest";
import {
  parseSkillCreatorLaunch,
  skillCreatorHref,
  skillCreatorPrompt,
} from "../src/lib/skill-creator-launch";

describe("Skill Creator launch", () => {
  it("keeps personal, platform, and Agent installation scopes distinct", () => {
    expect(skillCreatorHref("personal")).toContain("skillScope=personal");
    expect(skillCreatorHref("platform")).toContain("skillScope=platform");
    expect(skillCreatorHref("agent", {
      agentDraftId: "draft-1",
      agentLabel: "研究助手",
    })).toContain("agentDraft=draft-1");
  });

  it("defaults unknown launch scopes to personal", () => {
    const launch = parseSkillCreatorLaunch(
      new URLSearchParams("skill=skill-creator&skillScope=unknown"),
    );
    expect(launch?.scope).toBe("personal");
  });

  it("keeps the Skill invocation in removable context and prefills only editable intent", () => {
    const launch = parseSkillCreatorLaunch(
      new URLSearchParams(
        "skill=skill-creator&skillScope=agent&agentDraft=draft-1&agentLabel=%E7%A0%94%E7%A9%B6%E5%8A%A9%E6%89%8B",
      ),
    );
    expect(launch && skillCreatorPrompt(launch)).toBe(
      "请帮我创建一个Agent Skill（研究助手）：",
    );
  });
});
