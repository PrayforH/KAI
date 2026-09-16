import { describe, expect, it } from "vitest";
import { agentDisplayName } from "../src/lib/agent-display-name";

describe("platform assistant display name", () => {
  it("normalizes historical labels and identity-only bindings", () => {
    for (const label of ["通用 Lead Agent", "通用 lead-agent", "lead-agent"]) {
      expect(agentDisplayName("lead-agent", label)).toBe("通用助手");
    }
    expect(agentDisplayName("lead-agent")).toBe("通用助手");
  });
  it("preserves custom labels and other agent identities", () => {
    expect(agentDisplayName("lead-agent", "团队助手")).toBe("团队助手");
    expect(agentDisplayName("custom", "通用 Lead Agent")).toBe("通用 Lead Agent");
  });
});
