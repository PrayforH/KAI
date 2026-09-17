import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const detail = readFileSync(
  join(process.cwd(), "src/components/knowledge/knowledge-base-detail.tsx"),
  "utf8",
);

describe("knowledge base tabs", () => {
  it("only offers the Wiki and graph tabs for bases that carry a wiki", () => {
    // A RAG base answers those endpoints with "Wiki feature is not enabled",
    // which the console used to surface as a raw engine error.
    expect(detail).toContain('const supportsWiki = isWeknora && base?.kbType !== "rag";');
    const disabled = detail.match(/disabled=\{!supportsWiki\}/g) ?? [];
    expect(disabled).toHaveLength(2);
    expect(detail).not.toContain("disabled={!isWeknora}");
  });

  it("falls back to the documents tab when the base cannot serve Wiki", () => {
    expect(detail).toMatch(
      /if \(base && !supportsWiki && tab !== "docs"\) setTab\("docs"\);/,
    );
  });
});
