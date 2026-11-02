import { describe, expect, it } from "vitest";
import {
  botAppearanceTokens,
  contrastRatio,
  readableOn,
} from "../src/lib/bot-appearance";

describe("bot appearance tokens", () => {
  it("gives the white appearance a neutral grey surface and dark ink", () => {
    const tokens = botAppearanceTokens("#ffffff");
    expect(tokens.isLight).toBe(true);
    // 白本体坐在中性灰底板上（像 Grok 的白色外观），眼睛必须是深墨。
    expect(tokens.surface).toBe("#8e8e93");
    expect(tokens.ink).toBe("#151515");
    // 白底白字的气泡等于没有气泡：换成中性底板 + 深墨文字。
    expect(tokens.bubble).toContain("neutral-bubble");
    expect(tokens.bubbleInk).toContain("neutral-bubble-ink");
  });

  it("keeps dark accents transparent with light eyes", () => {
    const tokens = botAppearanceTokens("#126fd4");
    expect(tokens.isLight).toBe(false);
    expect(tokens.surface).toBe("transparent");
    expect(tokens.ink).toContain("255");
    expect(tokens.bubble).toBe("#126fd4");
    expect(tokens.bubbleInk).toBe("#ffffff");
  });

  it("keeps bot names readable on both themes", () => {
    for (const color of ["#ffffff", "#8e8e93", "#126fd4", "#ff9f0a", "#25c06a", "#a9734f"]) {
      const tokens = botAppearanceTokens(color);
      expect(contrastRatio(tokens.text, "#ffffff")).toBeGreaterThanOrEqual(4.5);
      expect(contrastRatio(tokens.textOnDark, "#101010")).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("keeps the accent itself when it already reads well", () => {
    expect(readableOn("#126fd4", "#ffffff")).toBe("#126fd4");
  });
});
