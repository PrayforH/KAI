// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { AssistantRuntimeProvider, useLocalRuntime } from "@assistant-ui/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChatReplyActions } from "../src/components/agent-thread";

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
        <ChatReplyActions threadId="thread-1" messageId="r1" text="第一段回答" />
      </div>
      <div data-bubble="r2">
        <ChatReplyActions threadId="thread-1" messageId="r2" text="第二段回答" />
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
