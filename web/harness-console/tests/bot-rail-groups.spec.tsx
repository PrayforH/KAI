import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, it } from "vitest";

const source = readFileSync(join(process.cwd(), "src/components/bot-rail.tsx"), "utf8");

it("groups bots by real status instead of a pinning concept", () => {
  // 「置顶」是误传的产品语义：分区只区分在岗（已启用）与待启用，不提供置顶。
  expect(source).toContain('item.status === "active"');
  expect(source).toContain("在岗");
  expect(source).toContain("待启用");
  expect(source).not.toContain("已置顶");
  expect(source).not.toContain("未分配");
});

it("keeps the main assistant pinned at the top and opens bots by full navigation", () => {
  const mainIndex = source.indexOf("主助理");
  const groupIndex = source.indexOf("在岗");
  expect(mainIndex).toBeGreaterThan(-1);
  expect(groupIndex).toBeGreaterThan(mainIndex);
  // 只改查询参数不会重挂载页面，必须整页导航，否则旧线程盖住新 bot。
  expect(source).toContain("window.location.assign(");
  expect(source).toContain("event.preventDefault()");
});
