// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AssistantComputerPane } from "../src/components/assistant-computer-pane";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const css = readFileSync(join(process.cwd(), "src/components/assistant-history-panel.module.css"), "utf8");
const paneCss = readFileSync(join(process.cwd(), "src/components/computer-desktop.module.css"), "utf8");
const page = readFileSync(join(process.cwd(), "src/app/page.tsx"), "utf8");

let root: Root | undefined;
let host: HTMLDivElement;

beforeEach(() => {
  // 电脑面板挂载时会探活；jsdom 里没有相对 URL 的 base，静默给个 401 即可。
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

const shares = () =>
  workspace().style.gridTemplateColumns
    .match(/([\d.]+)fr/g)!
    .map(value => Number(value.replace("fr", "")));

function render(element: ReactElement) {
  return act(async () => root!.render(element));
}

const workspace = () => host.querySelector<HTMLElement>("[data-history]")!;
const tracks = () => (workspace().style.gridTemplateColumns.match(/minmax\([^)]*\)|\d+px/g) ?? []);

describe("聊天记录是聊天区里的一列，不是浮层", () => {
  it("history is a leaf of the chat grid, next to chat and computer", async () => {
    await render(<AssistantComputerPane open onClose={() => {}} botId={null} historyOpen
      history={<aside data-testid="history">记录</aside>}>
      <section data-testid="chat">对话</section>
    </AssistantComputerPane>);

    const history = host.querySelector('[data-testid="history"]')!;
    // 面板在网格的直接子项（槽位）里，而不是浮在页面上的兄弟节点。
    expect(history.parentElement!.parentElement).toBe(workspace());
    expect(history.closest("body")!.querySelectorAll("[data-testid=history]")).toHaveLength(1);
    expect(workspace().dataset.history).toBe("true");
    // 聊天 | 历史 | 分隔条 | 电脑
    expect(tracks()).toHaveLength(4);
  });

  it("three columns are 38 / 32 / 30 at the default width", async () => {
    await render(<AssistantComputerPane open onClose={() => {}} botId={null} historyOpen
      history={<aside data-testid="history">记录</aside>}>
      <section data-testid="chat">对话</section>
    </AssistantComputerPane>);

    // 参考稿：聊天 38 / 历史 32 / 电脑 30，聊天列是最宽那栏。
    const [chat, history, computer] = shares();
    expect(chat).toBeCloseTo(38, 0);
    expect(history).toBe(32);
    expect(computer).toBeCloseTo(30, 0);
  });

  it("two columns default to 44 / 56", async () => {
    await render(<AssistantComputerPane open onClose={() => {}} botId={null}
      history={<aside data-testid="history">记录</aside>}>
      <section data-testid="chat">对话</section>
    </AssistantComputerPane>);

    const [chat, computer] = shares();
    expect(chat).toBe(44);
    expect(computer).toBe(56);
  });

  it("keeps the two-pane and three-pane computer widths independent", async () => {
    const pane = (historyOpen: boolean) => <AssistantComputerPane open onClose={() => {}} botId={null} historyOpen={historyOpen}
      history={<aside data-testid="history">记录</aside>}>
      <section data-testid="chat">对话</section>
    </AssistantComputerPane>;
    await render(pane(true));

    // 三栏把电脑列拖宽到 34（30 + 2 + 2），历史恒为 32，聊天吃剩余的 34。
    const separator = host.querySelector('[role=separator]')!;
    await act(async () => {
      separator.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }));
    });
    await act(async () => {
      separator.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }));
    });
    expect(shares()).toEqual([34, 32, 34]);

    // 关掉历史回到两栏：用回两栏自己的默认 44 / 56，不会跟着变成 30 左右的窄条。
    await render(pane(false));
    expect(shares()).toEqual([44, 56]);

    // 再打开历史：三栏记住刚才拖到的 34，不会跳回 30。
    await render(pane(true));
    expect(shares()).toEqual([34, 32, 34]);
  });

  it("history alone takes 54% and leaves chat 46%", async () => {
    await render(<AssistantComputerPane open={false} onClose={() => {}} botId={null} historyOpen
      history={<aside data-testid="history">记录</aside>}>
      <section data-testid="chat">对话</section>
    </AssistantComputerPane>);

    const [chat, history] = shares();
    expect(history).toBe(54);
    expect(chat).toBe(46);
  });

  it("the divider still drags the computer column in three-pane mode", async () => {
    await render(<AssistantComputerPane open onClose={() => {}} botId={null} historyOpen
      history={<aside data-testid="history">记录</aside>}>
      <section data-testid="chat">对话</section>
    </AssistantComputerPane>);

    const separator = host.querySelector('[role=separator]')!;
    expect(separator.getAttribute("aria-valuenow")).toBe("30");
    expect(separator.getAttribute("aria-valuemin")).toBe("25");
    expect(separator.getAttribute("aria-valuemax")).toBe("45");
    await act(async () => {
      separator.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }));
    });
    // 键盘 +2：电脑列 30 → 32，历史仍是 32，聊天接剩下的 36。
    expect(Number(separator.getAttribute("aria-valuenow"))).toBe(32);
    expect(shares()).toEqual([36, 32, 32]);
  });

  it("the drawer CSS no longer pins itself over the viewport", () => {
    expect(css).not.toMatch(/position:\s*fixed/);
    expect(css).not.toMatch(/box-shadow:\s*-18px/);
    expect(css).toMatch(/\.drawer\s*\{[^}]*position:\s*relative/);
    // 窄屏堆叠成行，分隔线转到上方。堆叠断点必须低于 1280，否则 1280 会白堆。
    expect(paneCss).toMatch(/data-history="true"\]\[data-split="true"\]/);
    expect(paneCss).toMatch(/@media \(max-width: 1180px\)/);
    expect(paneCss).not.toMatch(/@media \(max-width: 1300px\)/);
  });

  it("keeps exactly one history entry, in the floating controls", () => {
    // 助理面整块 header 不渲染，原来那颗「聊天记录」按钮永远不可见，已删。
    expect(page).not.toContain('aria-label="聊天记录"');
    expect(page).toContain("的聊天记录`");
    // 历史面板由网格拥有，不再作为兄弟节点浮在页面上。
    expect(page.match(/<AssistantHistoryPanel/g) ?? []).toHaveLength(1);
    expect(page).toContain("historyOpen={assistantView && !groupParam && historyOpen}");
  });
});
