// @vitest-environment jsdom
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { join } from "node:path";

describe("assistant page marks a view only when a bot conversation loads", () => {
  const page = readFileSync(join(process.cwd(), "src/app/page.tsx"), "utf8");

  it("posts the explicit viewed endpoint once per bot visit", () => {
    expect(page).toContain("/viewed`");
    expect(page).toContain("botViewedRef.current !== botId");
    // 只有拿到真实会话后才上报，避免仅停在空态页也算查看。
    const viewedIndex = page.indexOf("/viewed`");
    const threadIndex = page.indexOf("if (data.threadId) {");
    expect(threadIndex).toBeGreaterThan(-1);
    expect(viewedIndex).toBeGreaterThan(threadIndex);
  });
});
