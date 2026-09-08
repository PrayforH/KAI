import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const menu = readFileSync(
  join(process.cwd(), "src/components/account-menu.tsx"),
  "utf8",
);
const settings = readFileSync(
  join(process.cwd(), "src/app/settings/page.tsx"),
  "utf8",
);
const taskSidebar = readFileSync(
  join(process.cwd(), "src/components/task-sidebar.tsx"),
  "utf8",
);
const studioSidebar = readFileSync(
  join(process.cwd(), "src/components/agent-studio/studio-sidebar.tsx"),
  "utf8",
);
const workbench = readFileSync(join(process.cwd(), "src/app/page.tsx"), "utf8");
const styles = readFileSync(join(process.cwd(), "src/app/styles.css"), "utf8");
const login = readFileSync(
  join(process.cwd(), "src/app/login/page.tsx"),
  "utf8",
);
const memory = readFileSync(
  join(process.cwd(), "src/components/memory-bank/memory-bank.tsx"),
  "utf8",
);
const themeSelector = readFileSync(
  join(process.cwd(), "src/components/theme-toggle.tsx"),
  "utf8",
);
const productIcons = readFileSync(
  join(process.cwd(), "src/components/product-icon.tsx"),
  "utf8",
);

describe("account settings", () => {
  it("keeps settings and logout available from the account menu", () => {
    expect(menu).not.toContain("产品使用手册");
    expect(menu).not.toContain("DdiCdPFcroUpUXxOumNcQpIin1g");
    expect(menu).not.toContain('className="account-role"');
    expect(menu).toContain('href="/settings"');
    expect(menu).toContain("个人设置");
    expect(menu).toContain('href="/api/auth/logout"');
    expect(menu).toContain("退出登录");
    expect(menu).not.toContain('<ProductIcon name="book" />');
    expect(menu).toContain('<ProductIcon name="settings" />');
    expect(menu).toContain('<ProductIcon name="logout" />');
  });

  it("keeps governance links out of the simplified task account menu", () => {
    expect(menu).not.toContain('aria-label="资源与协作"');
    expect(menu).not.toContain('href: "/studio/files"');
    expect(menu).not.toContain('href: "/studio/capabilities"');
    expect(menu).not.toContain('href: "/studio/knowledge"');
    expect(menu).not.toContain('href: "/studio/spaces"');
    expect(styles).toContain(".account-workspaces");
    expect(styles).toContain(".account-workspace-link");
  });

  it("uses one custom line-icon system across account and settings navigation", () => {
    expect(productIcons).toContain("export type ProductIconName");
    expect(productIcons).toContain("data-product-icon={name}");
    expect(settings).toContain("const SETTINGS_NAV");
    expect(settings.match(/href: "#/g)).toHaveLength(10);
    expect(settings).toContain("<ProductIcon name={item.icon} />");
    expect(styles).toMatch(
      /\.account-actions svg\s*\{[^}]*stroke-width:\s*1\.65;/s,
    );
    expect(styles).toMatch(
      /\.settings-index a svg\s*\{[^}]*stroke-width:\s*1\.65;/s,
    );
  });

  it("keeps the settings header quiet and aligns control typography", () => {
    expect(settings).not.toContain("<ProductBrandMark");
    expect(settings).not.toContain("<ProductBrandCopy");
    expect(settings).toContain('className="settings-return-app"');
    expect(settings).toContain('返回应用');
    expect(styles).toMatch(
      /\.settings-back svg\s*\{[^}]*width:\s*18px;[^}]*height:\s*18px;/s,
    );
    expect(styles).toMatch(
      /\.archived-task-row button\s*\{[^}]*font:\s*600 12px\/1 var\(--codex-font-ui\);/s,
    );
    expect(styles).toMatch(
      /\.settings-dialog :where\(button, input, select, textarea\)\s*\{[^}]*font-family:\s*var\(--codex-font-ui\);/s,
    );
  });

  it("anchors the user control at the bottom of the expanded task rail", () => {
    expect(taskSidebar).toContain('className="task-sidebar-account"');
    expect(taskSidebar).not.toContain('className="task-rail-account"');
    expect(taskSidebar.match(/<AccountMenu \/>/g)).toHaveLength(1);
    expect(studioSidebar).toContain("<AccountMenu />");
    expect(workbench).not.toContain("<AccountMenu />");
    expect(styles).toMatch(/\.task-rail-account\s*\{[^}]*margin-top:\s*auto;/s);
    expect(styles).toMatch(
      /\.account-popover\s*\{[^}]*bottom:\s*calc\(100% \+ 8px\);[^}]*left:\s*0;[^}]*right:\s*0;[^}]*width:\s*auto;/s,
    );
    expect(menu).toContain('className="account-trigger-chevron"');
    expect(styles).toMatch(
      /@media \(max-width: 820px\)[\s\S]*?\.task-sidebar\.is-collapsed\s*\{[^}]*z-index:\s*20;/s,
    );
  });

  it("provides useful profile, password, and session controls", () => {
    expect(settings).toContain("个人资料");
    expect(settings).toContain("修改密码");
    expect(settings).toContain("登录会话");
    expect(settings).toContain('fetch("/api/auth/profile"');
    expect(settings).toContain('fetch("/api/auth/password"');
    expect(settings).toContain("修改密码后会撤销所有刷新会话");
  });

  it("keeps the only theme control in account appearance settings", () => {
    expect(settings).toMatch(
      /id:\s*"appearance",\s*href:\s*"#appearance",\s*label:\s*"外观"/s,
    );
    expect(settings).toContain('id="appearance"');
    expect(settings).toContain("<ThemeSelector />");
    expect(themeSelector).toContain('"浅色"');
    expect(themeSelector).toContain('"深色"');
    expect(workbench).not.toContain("ThemeSelector");
    expect(workbench).not.toContain("ThemeToggle");
    expect(studioSidebar).not.toContain("ThemeSelector");
    expect(studioSidebar).not.toContain("ThemeToggle");
    expect(login).not.toContain("ThemeSelector");
    expect(login).not.toContain("ThemeToggle");
    expect(memory).not.toContain("ThemeSelector");
    expect(memory).not.toContain("ThemeToggle");
  });

  it("uses a rounded paged settings dialog instead of one long document", () => {
    expect(settings).toContain('className="settings-dialog"');
    expect(settings).toContain(
      'aria-current={activeSection === item.id ? "page" : undefined}',
    );
    expect(settings).toMatch(
      /id="profile"[\s\S]*?hidden=\{activeSection !== "profile"\}/,
    );
    expect(styles).toMatch(
      /\.settings-dialog\s*\{[^}]*border-radius:\s*24px;/s,
    );
    expect(styles).toMatch(
      /\.settings-layout\s*\{[^}]*grid-template-columns:\s*220px minmax\(0,\s*1fr\);/s,
    );
    expect(styles).toMatch(/\.settings-content\s*\{[^}]*overflow-y:\s*auto;/s);
    expect(styles).toMatch(
      /@media \(max-width: 760px\)[\s\S]*?\.settings-dialog\s*\{[^}]*border-radius:\s*0;/s,
    );
    expect(styles).not.toContain(
      ".settings-shell {\n  background: linear-gradient",
    );
  });
});
