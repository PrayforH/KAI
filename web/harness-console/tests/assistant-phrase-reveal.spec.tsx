// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ActivityPhrase, phraseUnits, trimConclusion } from "../src/components/grok-activity";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const STEP = 90;

let root: Root | undefined;
let host: HTMLDivElement;

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined;
  host.remove();
  vi.useRealTimers();
});

function spans(): string[] {
  return [...host.querySelectorAll(".grok-phrase-word")].map((n) => n.textContent ?? "");
}

function phrase(): string {
  return host.querySelector(".grok-phrase")?.textContent ?? "";
}

async function tick(times = 1) {
  for (let index = 0; index < times; index += 1) {
    await act(async () => {
      vi.advanceTimersByTime(STEP);
    });
  }
}

describe("phrase reveal units", () => {
  it("uses graphemes for whitespace-free CJK text", () => {
    expect(phraseUnits("继续盯梢本轮：")).toEqual(["继", "续", "盯", "梢", "本", "轮", "："]);
  });

  it("keeps a whitespace-free English word as one token, not letters", () => {
    expect(phraseUnits("Reading")).toEqual(["Reading"]);
  });

  it("keeps word tokens for English with whitespace", () => {
    expect(phraseUnits("Reading the two files")).toEqual([
      "Reading",
      " ",
      "the",
      " ",
      "two",
      " ",
      "files",
    ]);
  });

  it("uses graphemes when CJK dominates even if a space appears", () => {
    expect(phraseUnits("先 确认参数")).toHaveLength(6);
  });
});

describe("ActivityPhrase reveals Chinese conclusions character by character", () => {
  it("shows the whole existing phrase first, then draws new CJK text one grapheme at a time", async () => {
    await act(async () => {
      root!.render(<ActivityPhrase text="继续" />);
    });
    // 挂载/历史文本一次给全，不重播动画。
    expect(phrase()).toBe("继续");
    expect(spans()).toHaveLength(2);

    vi.useFakeTimers();
    await act(async () => {
      root!.render(<ActivityPhrase text="继续盯梢本轮：" />);
    });
    // 共享前缀「继续」保持可见，其余逐字出现。
    expect(phrase()).toBe("继续");
    expect(spans()).toHaveLength(2);

    await tick();
    expect(phrase()).toBe("继续盯");
    await tick();
    expect(phrase()).toBe("继续盯梢");
    await tick(4);
    expect(phrase()).toBe("继续盯梢本轮：");
    expect(host.querySelector(".grok-phrase-caret")).toBeNull();
    expect(spans().every((unit) => [...unit].length === 1)).toBe(true);
  });

  it("draws a new Chinese phrase from the first character instead of pasting it whole", async () => {
    await act(async () => {
      root!.render(<ActivityPhrase text="收到快捷操作：" />);
    });
    vi.useFakeTimers();
    await act(async () => {
      root!.render(<ActivityPhrase text="参数已确认：" />);
    });
    // 前缀不同 → 从 0 开始画，不会整句一次性出现。
    expect(phrase()).toBe("");
    await tick();
    expect(phrase()).toBe("参");
    await tick(2);
    expect(phrase()).toBe("参数已");
  });
});

describe("ActivityPhrase keeps word-by-word reveal for English", () => {
  it("advances one token (word or its spacing) per step", async () => {
    await act(async () => {
      root!.render(<ActivityPhrase text="Reading" />);
    });
    vi.useFakeTimers();
    await act(async () => {
      root!.render(<ActivityPhrase text="Reading the two files" />);
    });
    expect(phrase()).toBe("Reading");

    await tick();
    expect(phrase()).toBe("Reading ");
    await tick();
    expect(phrase()).toBe("Reading the");
    await tick(4);
    expect(phrase()).toBe("Reading the two files");
  });
});

describe("progress summaries retain enough context", () => {
  it("keeps a complete readable sentence instead of ten characters", () => {
    const text = "我先盘点工作区里的文件，确认这次检查需要核对哪些材料。";
    expect(trimConclusion(text)).toBe(text);
    expect(phraseUnits(trimConclusion(text)).length).toBeGreaterThan(10);
  });
});
