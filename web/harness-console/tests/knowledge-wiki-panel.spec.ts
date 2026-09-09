import { describe, expect, it } from "vitest";
import { renderWikiContent } from "../src/components/knowledge/knowledge-wiki-panel";

describe("wiki content rendering", () => {
  it("splits paragraphs and resolves [[slug|label]] links", () => {
    const blocks = renderWikiContent(
      "# 以充值油卡为名的非法集资\n\n材料中的匿名涉案企业。\n\n相关专题：[[concept/fei-fa-ji-zi|非法集资]]。",
    );
    expect(blocks).toEqual([
      { kind: "heading", segments: [{ kind: "text", text: "以充值油卡为名的非法集资" }] },
      { kind: "text", segments: [{ kind: "text", text: "材料中的匿名涉案企业。" }] },
      {
        kind: "text",
        segments: [
          { kind: "text", text: "相关专题：" },
          { kind: "link", slug: "concept/fei-fa-ji-zi", label: "非法集资" },
          { kind: "text", text: "。" },
        ],
      },
    ]);
  });

  it("falls back to the slug when no label is given", () => {
    const blocks = renderWikiContent("[[summary/abc-123]]");
    expect(blocks).toEqual([
      {
        kind: "text",
        segments: [{ kind: "link", slug: "summary/abc-123", label: "summary/abc-123" }],
      },
    ]);
  });

  it("keeps multiple links on one line in order", () => {
    const blocks = renderWikiContent(
      "见 [[concept/a|甲]] 与 [[entity/b|乙]]。",
    );
    expect(blocks).toHaveLength(1);
    expect(blocks[0].segments.filter((segment) => segment.kind === "link")).toEqual([
      { kind: "link", slug: "concept/a", label: "甲" },
      { kind: "link", slug: "entity/b", label: "乙" },
    ]);
  });

  it("renders bold and inline code alongside links", () => {
    const lines = renderWikiContent("**预付费返利** 是 `concept` 里的 [[concept/a|甲]]。");
    expect(lines).toHaveLength(1);
    expect(lines[0].segments).toEqual([
      { kind: "bold", text: "预付费返利" },
      { kind: "text", text: " 是 " },
      { kind: "code", text: "concept" },
      { kind: "text", text: " 里的 " },
      { kind: "link", slug: "concept/a", label: "甲" },
      { kind: "text", text: "。" },
    ]);
  });

  it("returns no blocks for empty content", () => {
    expect(renderWikiContent("")).toEqual([]);
    expect(renderWikiContent("\n\n  \n")).toEqual([]);
  });
});
