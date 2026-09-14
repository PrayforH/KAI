import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const component = readFileSync(
  join(process.cwd(), "src/components/knowledge/knowledge-console.tsx"),
  "utf8",
);
const styles = readFileSync(
  join(process.cwd(), "src/components/knowledge/knowledge-console.module.css"),
  "utf8",
);
const client = readFileSync(
  join(process.cwd(), "src/lib/studio-client.ts"),
  "utf8",
);

describe("Knowledge console card menu", () => {
  it("moves member management and delete into a top-right ⋯ menu", () => {
    expect(component).toContain("cardMenuTrigger");
    expect(component).toContain("cardMenu");
    expect(component).toContain("成员管理");
    expect(component).toContain("删除知识库");
    // The inline member action is gone from the card meta row.
    expect(component).not.toContain('title="管理该知识库的成员与权限"');
  });

  it("closes the menu on outside clicks and Escape", () => {
    expect(component).toContain('document.addEventListener("click", close)');
    expect(component).toContain('document.addEventListener("keydown", onKey)');
    expect(component).toContain('event.key === "Escape"');
    expect(component).toContain('target.closest("[data-kb-menu]")');
  });

  it("warns that deleting also drops the remote WeKnora base", () => {
    expect(component).toContain("WeKnora 中该库及其全部文档、Wiki 与图谱将一并删除");
    expect(styles).toContain(".cardMenuItemDanger");
  });

  it("calls the DELETE base endpoint and tolerates its empty response", () => {
    expect(client).toContain("deleteKnowledgeBase: (reference: string)");
    expect(client).toMatch(/deleteKnowledgeBase[\s\S]{0,120}method: "DELETE"/);
  });
});
