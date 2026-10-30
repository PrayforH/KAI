import { renderToStaticMarkup } from "react-dom/server";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, it, vi } from "vitest";

const source = readFileSync(join(process.cwd(), "src/components/bot-edit-dialog.tsx"), "utf8");

vi.mock("../src/lib/assistant-client", () => ({
  assistantRequest: vi.fn(async () => ({
    specialist: {
      specialistId: "specialist_demo",
      name: "演示 Bot",
      description: "盯信号",
      status: "needs_input",
      revision: 2,
      config: { appearance: { color: "#ff2d95", avatar: "star" } },
    },
    duties: [],
  })),
}));

it("edit dialog offers the same 12-color × 6-shape picker as creation", async () => {
  const { BOT_COLORS, BOT_SHAPES } = await import("../src/components/create-bot-dialog");
  expect(BOT_COLORS).toHaveLength(12);
  expect(BOT_SHAPES).toHaveLength(6);
  // 编辑弹窗复用同一份色板/形状清单，而不是自带第二套。
  expect(source).toContain('from "./create-bot-dialog"');
  expect(source).toContain("BOT_COLORS.map");
  expect(source).toContain("BOT_SHAPES.map");
});

it("sends the chosen appearance through the profile API", () => {
  expect(source).toContain("appearance: { color, avatar }");
  expect(source).toContain("`specialists/${encodeURIComponent(specialistId)}/profile`");
});

it("seeds the picker from the stored appearance", () => {
  expect(source).toContain("appearance?.color");
  expect(source).toContain("appearance?.avatar");
});

it("marks routines as paused when the specialist reports an unviewed pause", () => {
  expect(source).toContain('reason === "unviewed"');
  expect(source).toContain("因无人查看已暂停，打开本会话后恢复");
  expect(source).toContain('data-paused={paused ? "true" : undefined}');
});
