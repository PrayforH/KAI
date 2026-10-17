import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const workbench = readFileSync(
  join(process.cwd(), "src/lib/agent-studio.ts"),
  "utf8",
);
const mcpManager = readFileSync(
  join(process.cwd(), "src/components/agent-studio/mcp-catalog-control-plane.tsx"),
  "utf8",
);
const integrations = readFileSync(
  join(process.cwd(), "src/components/agent-studio/agent-integrations.tsx"),
  "utf8",
);
const client = readFileSync(join(process.cwd(), "src/lib/studio-client.ts"), "utf8");

describe("MCP visibility is scoped to selected Agents", () => {
  it("filters the builder options by the draft Agent identity", () => {
    expect(workbench).toContain('option.visibility !== "selected"');
    expect(workbench).toContain("option.visibleAgentIds?.includes(draft.agentId)");
    expect(workbench).toContain('Pick<StudioDraft, "name" | "domain" | "agentId">');
  });

  it("filters the integrations drawer with the same rule", () => {
    expect(integrations).toContain('item.visibility !== "selected"');
    expect(integrations).toContain("item.visibleAgentIds?.includes(draft.agentId)");
  });

  it("lets MCP management choose all Agents or an explicit list", () => {
    expect(mcpManager).toContain("可见智能体");
    expect(mcpManager).toContain('value="selected"');
    expect(mcpManager).toContain("toggleVisibleAgent");
    expect(mcpManager).toContain("listAccessibleDrafts");
    expect(client).toContain("visibleAgentIds?: string[]");
  });
});
