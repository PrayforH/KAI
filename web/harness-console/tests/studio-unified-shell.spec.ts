import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const shell = readFileSync(
  join(process.cwd(), "src/components/agent-studio/studio-unified-shell.tsx"),
  "utf8",
);
const layout = readFileSync(
  join(process.cwd(), "src/app/studio/layout.tsx"),
  "utf8",
);
const manager = readFileSync(
  join(process.cwd(), "src/components/agent-studio/studio-capability-manager.tsx"),
  "utf8",
);
const sectionNavigation = readFileSync(
  join(process.cwd(), "src/components/agent-studio/studio-section-navigation.tsx"),
  "utf8",
);
const taskSidebar = readFileSync(
  join(process.cwd(), "src/components/task-sidebar.tsx"),
  "utf8",
);
const codex = readFileSync(
  join(process.cwd(), "src/app/web-codex.css"),
  "utf8",
);

describe("Studio unified shell", () => {
  it("reuses the conversation-window sidebar on Studio routes", () => {
    expect(layout).toContain("<StudioUnifiedShell>");
    expect(layout).toContain("<AuthProvider>");
    expect(shell).toContain("<TaskSidebar");
    expect(shell).toContain("activeNav");
    expect(shell).toContain('className="console-shell studio-unified"');
    expect(shell).toContain('className="studio-unified-content"');
  });

  it("highlights the active workspace item through activeNav", () => {
    expect(shell).toContain("usePathname");
    expect(shell).toContain('pathname.startsWith("/studio/skills")');
    expect(taskSidebar).toContain('active={activeNav}');
    expect(taskSidebar).toContain('visible={["knowledge", "agents", "automation", "skills"]}');
    expect(taskSidebar).not.toContain('capabilities');
  });

  it("styles the unified shell with the monochrome console gate", () => {
    expect(codex).toContain(".console-shell.studio-unified");
    expect(codex).toContain(".studio-unified-content");
    expect(shell).toContain(
      'className="header-icon-button header-sidebar-toggle studio-sidebar-expand"',
    );
    expect(codex).toMatch(
      /html\[data-color-mode="light"\][\s\S]*?\.task-list-item\.is-active\s*\{[^}]*background:\s*#e9e9eb;[^}]*box-shadow:\s*none;/s,
    );
  });

  it("shows Skill / MCP tabs only inside the capability workspace", () => {
    expect(manager).not.toContain("<StudioUnifiedShell");
    expect(manager).not.toContain("defaultTab");
    expect(manager).not.toContain("McpCatalogControlPlane");
    expect(manager).toContain("<StudioSectionNavigation");
    expect(sectionNavigation).toContain('href: "/studio/skills"');
    expect(sectionNavigation).not.toContain('/studio/capabilities');
    expect(sectionNavigation).not.toContain('href: "/studio/agents"');
    // The section switch now renders through the shared page header so its
    // tabs sit in the same place as the automation page's.
    expect(sectionNavigation).toContain('ariaLabel="技能与 MCP 管理"');
    expect(sectionNavigation).toContain("StudioPageHeaderLinks");
  });
});
