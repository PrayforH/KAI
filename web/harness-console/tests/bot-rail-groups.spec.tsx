import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, it } from "vitest";

const source = readFileSync(join(process.cwd(), "src/components/bot-rail.tsx"), "utf8");

it("keeps availability groups separate from user pinning", () => {
  // User pinning now organizes the rail; availability still comes from real status.
  expect(source).toContain('["active", "ready"].includes(item.status)');
  expect(source).toContain("可接单");
  expect(source).toContain("需要关注");
  expect(source).toContain("已置顶");
  expect(source).not.toContain("未分配");
});

it("keeps the main assistant pinned at the top and opens bots by full navigation", () => {
  const mainIndex = source.indexOf("主助理");
  const groupIndex = source.indexOf("可接单");
  expect(mainIndex).toBeGreaterThan(-1);
  expect(groupIndex).toBeGreaterThan(mainIndex);
  // 只改查询参数不会重挂载页面，必须整页导航，否则旧线程盖住新 bot。
  expect(source).toContain("window.location.assign(");
  expect(source).toContain("event.preventDefault()");
});
