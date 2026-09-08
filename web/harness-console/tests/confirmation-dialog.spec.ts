import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const dialog = readFileSync(
  join(process.cwd(), "src/components/confirmation-dialog.tsx"),
  "utf8",
);
const styles = readFileSync(
  join(process.cwd(), "src/components/confirmation-dialog.module.css"),
  "utf8",
);
const studio = readFileSync(
  join(
    process.cwd(),
    "src/components/agent-studio/agent-studio-workbench.tsx",
  ),
  "utf8",
);
const lifecycle = readFileSync(
  join(
    process.cwd(),
    "src/components/agent-studio/data-lifecycle-control-plane.tsx",
  ),
  "utf8",
);
const memory = readFileSync(
  join(process.cwd(), "src/components/memory-bank/memory-bank.tsx"),
  "utf8",
);

describe("shared confirmation dialog", () => {
  it("uses an accessible modal contract with safe default focus", () => {
    expect(dialog).toContain('role="alertdialog"');
    expect(dialog).toContain('aria-modal="true"');
    expect(dialog).toContain("aria-labelledby={titleId}");
    expect(dialog).toContain("aria-describedby={descriptionId}");
    expect(dialog).toContain("cancelRef.current?.focus()");
    expect(dialog).toContain('event.key === "Escape"');
    expect(dialog).toContain('event.key !== "Tab"');
    expect(dialog).toContain("previousFocusRef.current?.focus()");
    expect(dialog).toContain("createPortal");
    expect(dialog).toContain('type ConfirmationDecision = "confirm" | "cancel" | "discard"');
    expect(dialog).toContain("request.discardLabel");
  });

  it("supports theme tokens, mobile layout and reduced motion", () => {
    expect(styles).toContain("var(--codex-panel-solid)");
    expect(styles).toContain("var(--codex-danger)");
    expect(styles).toContain("@media (max-width: 520px)");
    expect(styles).toContain("@media (prefers-reduced-motion: reduce)");
    expect(styles).toContain("translateY(1px) scale(.98)");
  });

  it("replaces every native browser confirmation on product surfaces", () => {
    for (const source of [studio, lifecycle, memory]) {
      expect(source).not.toContain("window.confirm");
      expect(source).toContain("useConfirmationDialog");
    }
    expect(studio).toContain("保存并切换");
    expect(studio).toContain("放弃本地修改并加载控制面版本？");
    expect(studio).toContain("放弃并加载");
    expect(studio).toContain("继续导入");
    expect(studio).toContain("放弃修改并离开");
    expect(studio).toContain("卸载 Skill");
    expect(lifecycle).toContain("确认删除");
    expect(memory).toContain("删除记忆");
  });
});
