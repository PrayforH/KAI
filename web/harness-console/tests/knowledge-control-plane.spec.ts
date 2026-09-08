import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const component = readFileSync(
  join(process.cwd(), "src/components/agent-studio/mcp-catalog-control-plane.tsx"),
  "utf8",
);
const page = readFileSync(
  join(process.cwd(), "src/app/studio/knowledge/page.tsx"),
  "utf8",
);
const consolePage = readFileSync(
  join(process.cwd(), "src/components/knowledge/knowledge-console.tsx"),
  "utf8",
);
const layout = readFileSync(
  join(process.cwd(), "src/app/studio/layout.tsx"),
  "utf8",
);
const styles = readFileSync(
  join(process.cwd(), "src/components/agent-studio/mcp-catalog-control-plane.module.css"),
  "utf8",
);

describe("Knowledge control plane", () => {
  it("is a first-class Studio page for card-based knowledge management", () => {
    expect(page).toContain("<KnowledgeConsole");
    expect(layout).toContain("<StudioUnifiedShell>");
    expect(page).not.toContain("<StudioUnifiedShell");
    expect(consolePage).toContain("新建知识库");
    expect(consolePage).toContain("engine: \"weknora\"");
    expect(consolePage).toContain("hybrid");
  });

  it("uses governed MCP registration and manual tool discovery", () => {
    expect(component).toContain('membership.role !== "viewer"');
    expect(component).toContain("studioClient.discoverMcp");
    expect(component).toContain("检测地址");
    expect(component).toContain("已选择 {draft.tools.length} 个");
  });

  it("scopes catalog entries to the knowledge category", () => {
    expect(component).toContain('category === category');
  });

  it("uses the shared centered management width", () => {
    expect(styles).toContain("width: min(1160px, 100%)");
    expect(styles).toContain("catalogToolbar");
  });
});
