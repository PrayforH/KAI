import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const page = readFileSync(join(process.cwd(), "src/app/settings/page.tsx"), "utf8");
const component = readFileSync(
  join(process.cwd(), "src/components/workspace-members.tsx"),
  "utf8",
);
const styles = readFileSync(
  join(process.cwd(), "src/components/workspace-members.module.css"),
  "utf8",
);

describe("Workspace member management", () => {
  it("hides the member entry and avoids mounting member data in personal settings", () => {
    expect(page).not.toContain('href: "#members"');
    expect(page).not.toContain("<WorkspaceMembers");
    expect(page).toContain("显示内部子智能体");
  });

  it("lists members and saves deliberate role changes", () => {
    expect(component).toContain('fetch("/api/auth/members"');
    expect(component).toContain('method: "PATCH"');
    expect(component).toContain("role === member.membership.role");
    expect(component).toContain("角色变更会写入审计日志");
    expect(component).toContain("protectedFromAdmin");
    expect(component).toContain("? [member.membership.role]");
  });

  it("explains permissions instead of hiding the section", () => {
    expect(component).toContain("成员角色由 Owner / Admin 管理");
    expect(styles).toContain(".restricted");
    expect(styles).toContain("@media(max-width:680px)");
  });

  it("uses global Codex tokens in dark and light themes", () => {
    expect(styles).toContain("var(--codex-surface-raised,#242424)");
    expect(styles).toContain("var(--codex-field,#191919)");
    expect(styles).not.toContain("var(--panel,#fff)");
  });
});
