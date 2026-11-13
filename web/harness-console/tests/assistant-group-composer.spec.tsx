// @vitest-environment jsdom
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AssistantGroupComposer } from "../src/components/assistant-group-composer";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

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
});

const members = [
  { memberId: "primary", name: "主助理" },
  { memberId: "kernel", name: "Agent 内核" },
];

type HarnessProps = {
  initial?: string;
  quoted?: { authorName: string; text: string } | null;
  sending?: boolean;
  onSend?: () => void;
  onMention?: (name: string) => void;
  onClearQuote?: () => void;
};

function Harness({
  initial = "",
  quoted = null,
  sending = false,
  onSend = vi.fn(),
  onMention,
  onClearQuote = vi.fn(),
}: HarnessProps) {
  const [value, setValue] = useState(initial);
  return (
    <AssistantGroupComposer
      value={value}
      onChange={setValue}
      onSend={onSend}
      onMention={onMention}
      sending={sending}
      placeholder="给 演示群 发消息"
      members={members}
      quoted={quoted}
      onClearQuote={onClearQuote}
    />
  );
}

function type(value: string) {
  const input = host.querySelector<HTMLTextAreaElement>("textarea")!;
  const setter = Object.getOwnPropertyDescriptor(
    HTMLTextAreaElement.prototype,
    "value",
  )!.set!;
  return act(async () => {
    setter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

describe("AssistantGroupComposer", () => {
  it("starts as a single-line input with a disabled send button", async () => {
    await act(async () => root!.render(<Harness />));
    const input = host.querySelector<HTMLTextAreaElement>("textarea")!;
    expect(input.rows).toBe(1);
    expect(input.placeholder).toBe("给 演示群 发消息");
    expect(host.querySelector<HTMLButtonElement>('[aria-label="发送"]')!.disabled).toBe(true);
  });

  it("enables send once there is text and calls onSend on Enter", async () => {
    const onSend = vi.fn();
    await act(async () => root!.render(<Harness onSend={onSend} />));
    await type("你好");
    const send = host.querySelector<HTMLButtonElement>('[aria-label="发送"]')!;
    expect(send.disabled).toBe(false);
    await act(async () => {
      host
        .querySelector("textarea")!
        .dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    });
    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it("keeps Shift+Enter for a new line", async () => {
    const onSend = vi.fn();
    await act(async () => root!.render(<Harness onSend={onSend} />));
    await type("第一行");
    await act(async () => {
      host
        .querySelector("textarea")!
        .dispatchEvent(
          new KeyboardEvent("keydown", { key: "Enter", shiftKey: true, bubbles: true }),
        );
    });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("shows the one-line quote bar and clears it", async () => {
    const onClearQuote = vi.fn();
    await act(async () =>
      root!.render(
        <Harness
          quoted={{ authorName: "Agent 内核", text: "上一句被引用的内容" }}
          onClearQuote={onClearQuote}
        />,
      ),
    );
    expect(host.textContent).toContain("Agent 内核");
    expect(host.textContent).toContain("上一句被引用的内容");
    await act(async () =>
      host.querySelector<HTMLButtonElement>('[aria-label="取消引用"]')!.click(),
    );
    expect(onClearQuote).toHaveBeenCalledTimes(1);
  });

  it("inserts a mention from the @ menu", async () => {
    const onMention = vi.fn();
    await act(async () => root!.render(<Harness onMention={onMention} />));
    const trigger = host.querySelector<HTMLButtonElement>('[aria-label="提到群成员"]')!;
    expect(trigger.disabled).toBe(false);
    await act(async () => trigger.click());
    const items = host.querySelectorAll<HTMLButtonElement>(
      '[aria-label="选择要提到的成员"] button',
    );
    expect(items).toHaveLength(2);
    await act(async () => items[1].click());
    expect(onMention).toHaveBeenCalledWith("Agent 内核");
  });

  it("does not send while a send is in flight", async () => {
    const onSend = vi.fn();
    await act(async () => root!.render(<Harness initial="你好" sending onSend={onSend} />));
    expect(host.querySelector<HTMLButtonElement>('[aria-label="发送"]')!.disabled).toBe(true);
    await act(async () => {
      host
        .querySelector("textarea")!
        .dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
    });
    expect(onSend).not.toHaveBeenCalled();
  });
});
