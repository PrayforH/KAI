// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BotSidePanel } from "../src/components/bot-side-panel";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const source = readFileSync(join(process.cwd(), "src/components/bot-side-panel.tsx"), "utf8");

let root: Root | undefined;
let host: HTMLDivElement;

beforeEach(() => {
  // 电脑卡片挂载时探活；jsdom 里没有相对 URL 的 base，静默给个 401 即可。
  vi.stubGlobal("fetch", vi.fn(async () => ({
    ok: false, status: 401, json: async () => ({}),
  })));
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined;
  host.replaceChildren();
  vi.unstubAllGlobals();
});

function render(element: ReactElement) {
  return act(async () => root!.render(element));
}

describe("助理右侧栏不再承载聊天记录", () => {
  it.each([true, false])("keeps only 动态 / 任务 / 资料库 (primary=%s)", async (primary) => {
    await render(<BotSidePanel botId="specialist_demo" primary={primary} name="演示助理" tone="#126fd4"
      threadId="bot-thread" ready onClose={vi.fn()} onComputer={vi.fn()} />);

    const tabs = [...host.querySelectorAll<HTMLElement>('[role="tab"]')].map(tab => tab.textContent);
    expect(tabs).toEqual(["动态", "任务", "资料库"]);
    expect(host.textContent).not.toContain("聊天记录");
    // 聊天记录由消息区那颗独立 history 按钮打开，右侧栏里不再有第二个入口。
    expect(host.querySelector('[aria-label*="聊天记录"]')).toBeNull();
  });

  it("drops the history tab and its thread-history loader from the source", () => {
    expect(source).not.toContain('label: "聊天记录"');
    expect(source).not.toContain('{ key: "history"');
    expect(source).not.toContain("BotHistory");
    expect(source).not.toContain("loadFullThreadHistory");
    expect(source).not.toContain("ChatHistoryList");
  });
});
