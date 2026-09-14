import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const layout = readFileSync(join(process.cwd(), "src/app/layout.tsx"), "utf8");
const theme = readFileSync(
  join(process.cwd(), "src/app/weknora-theme.css"),
  "utf8",
);
const themeSelector = readFileSync(
  join(process.cwd(), "src/components/theme-toggle.tsx"),
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
const studioStyles = readFileSync(
  join(process.cwd(), "src/components/agent-studio/agent-studio.module.css"),
  "utf8",
);
const workspaceNavigationStyles = readFileSync(
  join(process.cwd(), "src/components/workspace-navigation.module.css"),
  "utf8",
);
const codexStyles = readFileSync(
  join(process.cwd(), "src/app/codex-theme.css"),
  "utf8",
);
const webCodexStyles = readFileSync(
  join(process.cwd(), "src/app/web-codex.css"),
  "utf8",
);
const loginStyles = readFileSync(
  join(process.cwd(), "src/app/login-kimi.css"),
  "utf8",
);

describe("Weknora-inspired product theme", () => {
  it("loads the product layer after the legacy theme and identifies the UI generation", () => {
    expect(layout.indexOf('import "./weknora-theme.css"')).toBeGreaterThan(
      layout.indexOf('import "./codex-theme.css"'),
    );
    expect(layout).toContain('data-product-ui="xushu"');
    expect(layout).toContain('color: "#ffffff"');
  });

  it("keeps the legacy product layer while the workbench uses a Doubao-like light shell", () => {
    expect(theme).toMatch(
      /html\[data-color-mode="light"\]\s*\{[^}]*--codex-surface:\s*#fbfcfb;[^}]*--codex-accent:\s*#16b364;/s,
    );
    expect(theme).toMatch(
      /html\[data-color-mode="light"\] body::before\s*\{[^}]*display:\s*none;/s,
    );
    expect(themeSelector).toContain("清爽白底与克制蓝色强调");
    expect(webCodexStyles).toMatch(
      /html\[data-color-mode="light"\] body\.codex-theme-v1\s*\{[^}]*--codex-surface:\s*#ffffff;[^}]*--codex-surface-sidebar:\s*#f7f7f8;[^}]*--codex-accent:\s*#2f75e8;/s,
    );
    expect(webCodexStyles).toMatch(
      /Doubao-inspired light task shell[\s\S]*?\.task-list-item\.is-active\s*\{[^}]*background:\s*#e9e9eb;[^}]*box-shadow:\s*none;/s,
    );
    expect(webCodexStyles).toMatch(
      /data-color-mode="light"[^}]*\.harness-composer-shell \.aui-composer-root\s*\{[^}]*border-radius:\s*16px;[^}]*background:\s*#ffffff;[^}]*box-shadow:/s,
    );
    expect(webCodexStyles).not.toContain("--aui-thread-max-width: 980px");
    expect(webCodexStyles).not.toContain("width: min(980px, 100%)");
    // Both appearances share one navigation geometry. The light branch only
    // repaints, so the project tree keeps the dark indentation grid and the
    // status dot keeps its dark offset instead of being restyled per theme.
    expect(webCodexStyles).toMatch(
      /data-color-mode="light"[^}]*\.task-project-heading strong\s*\{\s*color:\s*#56565b;\s*\}/s,
    );
    expect(webCodexStyles).toMatch(
      /data-color-mode="light"[^}]*\.task-workbench-name strong\s*\{\s*color:\s*#1c1c1f;\s*\}/s,
    );
    expect(webCodexStyles).not.toContain("margin: 2px 0 7px 24px;");
    expect(webCodexStyles).not.toContain("left: 11px;");
    // The shared row box stays the single definition, and no light selector
    // re-declares it.
    expect(webCodexStyles).toMatch(
      /min-height:\s*30px;\s*padding:\s*5px 6px 5px 35px;/,
    );
    expect(webCodexStyles).not.toMatch(
      /html\[data-color-mode="light"\][^{]*\.task-list-item[^{]*\{[^}]*padding/s,
    );
    expect(webCodexStyles).not.toMatch(
      /html\[data-color-mode="light"\][^{]*\.task-list-item[^{]*\{[^}]*min-height/s,
    );
  });

  it("flattens and enlarges high-frequency sidebar navigation", () => {
    expect(workspaceNavigationStyles).toMatch(
      /\.navigationLink,\s*\.navigationActive\s*\{[^}]*min-height:\s*40px;[^}]*font-size:\s*12\.5px;/s,
    );
    expect(workspaceNavigationStyles).toMatch(
      /data-color-mode="light"[^}]*\.navigationActive\s*\{[^}]*box-shadow:\s*none;/s,
    );
    expect(taskSidebar).not.toContain("智能任务工作台");
    expect(studioSidebar).not.toContain("智能体控制面");
  });

  it("keeps the light navigation on the dark row density and type scale", () => {
    // The task rail used to carry a light-only skin with taller rows, smaller
    // and bolder titles and a tinted 新建任务 pill. Light now inherits the
    // shared density and repaints only, so neither layer may re-declare the
    // sidebar's geometry or type while theming it.
    for (const geometry of [
      "min-height: 60px;",
      "font-size: 12.5px;\n  font-weight: 590;",
      "min-height: 42px;",
      "min-height: 64px;",
    ]) {
      expect(theme).not.toContain(geometry);
    }
    expect(theme).not.toMatch(/data-color-mode="light"[^}]*\.task-sidebar-primary button/);
    expect(theme).not.toMatch(/data-color-mode="light"[^}]*\.task-list-title\s*\{/);
    expect(webCodexStyles).not.toMatch(
      /data-color-mode="light"[^}]*\.task-project-items\s*\{\s*margin-left:/s,
    );
    expect(theme).toMatch(
      /html\[data-color-mode="light"\] body\.codex-theme-v1 \.task-sidebar-account\s*\{/s,
    );
  });

  it("keeps settings and Studio on the same quiet surface hierarchy", () => {
    expect(theme).toMatch(
      /\.settings-layout\s*\{[^}]*grid-template-columns:\s*220px minmax\(0, 1fr\);/s,
    );
    expect(theme).toMatch(
      /\.settings-form\s*\{[^}]*border-radius:\s*12px;[^}]*background:\s*#ffffff;/s,
    );
    expect(studioStyles).toMatch(
      /data-color-mode="light"[^}]*\.workspaceTabActive,[\s\S]*?box-shadow:\s*none;/,
    );
    expect(studioStyles).toContain("--studio-green: var(--codex-accent)");
  });

  it("keeps the Kimi-inspired login in one focused dark surface", () => {
    expect(loginStyles).toMatch(
      /Kimi-inspired login:[\s\S]*?data-color-mode="light"[^}]*\.login-shell\s*\{[^}]*--login-background:\s*#080909;[^}]*--login-card:\s*#181818;/s,
    );
  });

  it("keeps the selected Agent readable with a compact green state rail", () => {
    expect(studioStyles).toMatch(
      /\.agentRowActive,[\s\S]*?inset 3px 0 0 var\(--studio-green\)/,
    );
    expect(studioStyles).toMatch(
      /\.agentRowActive \.agentRowCopy strong,[\s\S]*?color:\s*var\(--studio-ink\);/,
    );
  });

  it("keeps the Markdown prompt editor on the light Studio surface", () => {
    expect(studioStyles).toMatch(
      /data-color-mode="light"[^}]*\.codeEditor\s*\{[^}]*color:\s*var\(--studio-ink-soft\);[^}]*background:\s*var\(--studio-panel\);/s,
    );
    expect(studioStyles).toMatch(
      /data-color-mode="light"[^}]*\.codeEditor:read-only\s*\{[^}]*background:\s*var\(--studio-panel-subtle\);/s,
    );
  });
});
