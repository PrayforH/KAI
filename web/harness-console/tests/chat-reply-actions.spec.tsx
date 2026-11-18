// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { AssistantRuntimeProvider, useLocalRuntime } from "@assistant-ui/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChatReplyActions, ChatReplyReactions } from "../src/components/agent-thread";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
vi.stubGlobal("ResizeObserver", class {
  observe() {}
  unobserve() {}
  disconnect() {}
});
vi.stubGlobal("matchMedia", () => ({
  matches: false,
  addEventListener() {},
  removeEventListener() {},
}));

let root: Root | undefined;
let host: HTMLDivElement;

beforeEach(() => {
  window.localStorage.clear();
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined;
  host.remove();
  window.localStorage.clear();
  vi.restoreAllMocks();
});

function Bubbles() {
  const runtime = useLocalRuntime({
    async *run() {
      yield { content: [{ type: "text" as const, text: "unused" }] };
    },
  });
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <div data-bubble="r1">
        <ChatReplyActions threadId="thread-1" messageId="r1" text="第一段回答" renderReactions={false} />
        <ChatReplyReactions threadId="thread-1" messageId="r1" />
      </div>
      <div data-bubble="r2">
        <ChatReplyActions threadId="thread-1" messageId="r2" text="第二段回答" renderReactions={false} />
        <ChatReplyReactions threadId="thread-1" messageId="r2" />
      </div>
    </AssistantRuntimeProvider>
  );
}

async function renderTwoBubbles() {
  await act(async () => root!.render(<Bubbles />));
}

function bubble(id: string): HTMLElement {
  return host.querySelector<HTMLElement>(`[data-bubble="${id}"]`)!;
}

describe("assistant chat bubble reactions", () => {
  it("adds a reaction to the clicked bubble only", async () => {
    await renderTwoBubbles();
    const trigger = bubble("r1").querySelector<HTMLButtonElement>(
      '[aria-label="给这条消息加表情"]',
    )!;
    await act(async () => trigger.click());
    const picker = bubble("r1").querySelector<HTMLElement>('[aria-label="选择表情"]')!;
    await act(async () => picker.querySelectorAll<HTMLButtonElement>("button")[0].click());

    expect(bubble("r1").querySelector('[aria-pressed="true"]')?.textContent).toBe("👍");
    expect(bubble("r2").querySelector('[aria-pressed="true"]')).toBeNull();

    const stored = JSON.parse(
      window.localStorage.getItem("kai:assistant-message-local:thread-1") ?? "{}",
    );
    expect(stored.reactions).toEqual({ r1: ["👍"] });
  });

  it("renders the chips on their own row below the bubble, not in the side bar", async () => {
    await renderTwoBubbles();
    const trigger = bubble("r1").querySelector<HTMLButtonElement>(
      '[aria-label="给这条消息加表情"]',
    )!;
    await act(async () => trigger.click());
    const picker = bubble("r1").querySelector<HTMLElement>('[aria-label="选择表情"]')!;
    await act(async () => picker.querySelectorAll<HTMLButtonElement>("button")[0].click());

    const chip = bubble("r1").querySelector<HTMLElement>('[aria-pressed="true"]')!;
    // 表情行独立于操作条：不在操作条所在的 [data-visible] 容器里。
    expect(chip.parentElement?.className).toContain("assistant-reply-reactions");
    expect(chip.closest("[data-visible]")).toBeNull();
    // 操作条本身不再渲染已选表情，避免侧边和下方各一份。
    expect(trigger.parentElement?.parentElement?.querySelector('[aria-pressed="true"]')).toBeNull();
  });

  it("keeps chips inline in the action bar when the surface does not split them", async () => {
    function Inline() {
      const runtime = useLocalRuntime({
        async *run() {
          yield { content: [{ type: "text" as const, text: "unused" }] };
        },
      });
      return (
        <AssistantRuntimeProvider runtime={runtime}>
          <div data-bubble="inline">
            <ChatReplyActions threadId="thread-2" messageId="i1" text="任务回答" />
          </div>
        </AssistantRuntimeProvider>
      );
    }
    await act(async () => root!.render(<Inline />));
    const trigger = host.querySelector<HTMLButtonElement>(
      '[aria-label="给这条消息加表情"]',
    )!;
    await act(async () => trigger.click());
    const picker = host.querySelector<HTMLElement>('[aria-label="选择表情"]')!;
    await act(async () => picker.querySelectorAll<HTMLButtonElement>("button")[0].click());

    const chip = host.querySelector<HTMLElement>('[aria-pressed="true"]')!;
    // 任务页保持原样：chip 仍在该操作条的纵向堆叠里。
    expect(chip.closest('[class*="stack"]')).not.toBeNull();
  });

  it("keeps the reaction on the same bubble after a remount", async () => {
    await renderTwoBubbles();
    const trigger = bubble("r2").querySelector<HTMLButtonElement>(
      '[aria-label="给这条消息加表情"]',
    )!;
    await act(async () => trigger.click());
    const picker = bubble("r2").querySelector<HTMLElement>('[aria-label="选择表情"]')!;
    await act(async () => picker.querySelectorAll<HTMLButtonElement>("button")[1].click());

    await act(async () => root!.unmount());
    root = createRoot(host);
    await renderTwoBubbles();

    expect(bubble("r2").querySelector('[aria-pressed="true"]')?.textContent).toBe("❤️");
    expect(bubble("r1").querySelector('[aria-pressed="true"]')).toBeNull();
  });
});
