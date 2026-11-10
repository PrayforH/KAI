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

describe("one computer per person, reachable from every assistant conversation", () => {
  const page = readFileSync(join(process.cwd(), "src/app/page.tsx"), "utf8");

  it("mounts the pane for the assistant entry, not only the main thread", () => {
    // 电脑属于这个人：主助理与子助理会话都能打开同一台；控制权靠 desktop 租约独占。
    expect(page).toContain("<AssistantComputerPane open={assistantView && computerOpen}");
    expect(page).not.toContain("<AssistantComputerPane open={personalAssistant && computerOpen}");
  });

  it("keeps the computer control on the same surface", () => {
    // 助理面板里的「打开电脑」和自动展开共用 showComputer，只有助理面才挂这块面板。
    expect(page).toContain("{assistantRailOpen && (");
    expect(page).toContain("onComputer={() => showComputer(true)}");
    expect(page).not.toContain("onComputer={personalAssistant");
  });
});
