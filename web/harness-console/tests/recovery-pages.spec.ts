import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const errorPage = readFileSync(join(process.cwd(), "src/app/error.tsx"), "utf8");
const notFoundPage = readFileSync(join(process.cwd(), "src/app/not-found.tsx"), "utf8");
const styles = readFileSync(join(process.cwd(), "src/app/styles.css"), "utf8");
const knowledgePage = readFileSync(join(process.cwd(), "src/app/studio/knowledge/page.tsx"), "utf8");

describe("release recovery surfaces", () => {
  it("offers direct recovery without implying saved work was lost", () => {
    expect(errorPage).toContain("已保存的任务和工作区记录不会受影响");
    expect(errorPage).toContain("onClick={reset}");
    expect(errorPage).toContain('href="/"');
    expect(notFoundPage).toContain("返回任务");
    expect(notFoundPage).toContain('href="/studio/agents"');
  });

  it("keeps recovery pages responsive and keyboard visible", () => {
    expect(styles).toContain(".recovery-page");
    expect(styles).toContain("min-height: 100dvh");
    expect(styles).toContain(".recovery-primary:focus-visible");
    expect(styles).toContain("@media (max-width: 520px)");
  });

  it("lets the root title template add the brand exactly once", () => {
    expect(knowledgePage).toContain('title: "知识库"');
    expect(knowledgePage).not.toContain("知识库 · Agent Studio");
  });
});
