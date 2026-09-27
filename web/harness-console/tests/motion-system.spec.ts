import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const appDir = join(process.cwd(), "src/app");
const codexTheme = readFileSync(join(appDir, "codex-theme.css"), "utf8");
const conversation = readFileSync(join(appDir, "conversation-experience.css"), "utf8");
const styles = readFileSync(join(appDir, "styles.css"), "utf8");

describe("global motion system", () => {
  it("defines a shared rhythm for controls, content, and panels", () => {
    expect(codexTheme).toContain("--codex-motion-fast: 120ms");
    expect(codexTheme).toContain("--codex-motion-control: 160ms");
    expect(codexTheme).toContain("--codex-motion-content: 220ms");
    expect(codexTheme).toContain("--codex-motion-panel: 280ms");
    expect(codexTheme).toContain("--codex-motion-ease:");
  });

  it("animates surfaces and activity states without introducing a dependency", () => {
    expect(codexTheme).toContain("@keyframes codex-surface-in");
    expect(codexTheme).toContain(".codex-motion-state");
    expect(conversation).toContain(".activity-row");
    expect(codexTheme).toContain("@keyframes codex-state-in");
    expect(conversation).toContain(".workbench-rail-panel");
    expect(conversation).not.toMatch(/\.tool-card,\s*body\.codex-theme-v1 \.agent-card\s*\{\s*animation:/);
  });

  it("keeps reduced motion immediate while retaining semantic state", () => {
    expect(styles).toContain("@media (prefers-reduced-motion: reduce)");
    expect(styles).toContain(".codex-motion-state");
    expect(styles).toContain("transform: none !important");
    expect(styles).toContain("animation: none !important");
  });
});
