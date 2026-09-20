import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const header = readFileSync(
  join(process.cwd(), "src/components/agent-studio/studio-page-header.tsx"),
  "utf8",
);
const headerCss = readFileSync(
  join(process.cwd(), "src/components/agent-studio/studio-page-header.module.css"),
  "utf8",
);

function read(path: string) {
  return readFileSync(join(process.cwd(), path), "utf8");
}

describe("shared studio page header", () => {
  it("keeps the section tabs pinned in the page header", () => {
    expect(header).toContain('role="tablist"');
    expect(header).toContain('role="tab"');
    expect(header).toContain("aria-selected");
    expect(header).toContain("StudioPageHeaderLinks");
    expect(headerCss).toContain("position: sticky");
    expect(headerCss).toContain("top: 0");
  });

  it("themes both colour modes through tokens", () => {
    expect(headerCss).toContain("--pgh-ink");
    expect(headerCss).toContain("var(--codex-ink");
    expect(headerCss).toContain('html[data-color-mode="light"]');
  });

  it("is the layout the 技能/MCP switch uses", () => {
    expect(read("src/components/agent-studio/studio-section-navigation.tsx")).toContain(
      "<StudioPageHeaderLinks",
    );
    // The old underline bar must not come back.
    expect(read("src/components/agent-studio/studio-section-navigation.module.css")).not.toContain(
      "border-bottom",
    );
  });

  it("leaves the automation page and the knowledge page on their own layouts", () => {
    // Both pages were reverted to their own headers: the shared sticky header
    // cost the knowledge page its title block and overlapped the automation
    // toolbar.
    const automation = read("src/components/agent-studio/automation-manager.tsx");
    const knowledge = read("src/components/knowledge/knowledge-console.tsx");
    expect(automation).not.toContain("<StudioPageHeader");
    expect(automation).toContain("<header className={styles.header}>");
    expect(knowledge).not.toContain("<StudioPageHeader");
    expect(knowledge).toContain("styles.hero");
    expect(knowledge).toContain("新建知识库");
  });
});
