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
  hint?: string;
};

function Harness({
  initial = "",
  quoted = null,
  sending = false,
  onSend = vi.fn(),
  onMention,
  onClearQuote = vi.fn(),
  hint,
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
      hint={hint}
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

  it("ignores Enter while a Chinese IME is composing", async () => {
    const onSend = vi.fn();
    await act(async () => root!.render(<Harness onSend={onSend} />));
    await type("你好");
    const input = host.querySelector("textarea")!;
    await act(async () => {
      input.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Enter", isComposing: true, bubbles: true }),
      );
    });
    await act(async () => {
      input.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Enter", keyCode: 229, bubbles: true }),
      );
    });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("keeps the hint outside the composer card", async () => {
    await act(async () => root!.render(<Harness hint="Enter 发送" />));
    const hint = host.querySelector('[data-testid="group-composer-hint"]')!;
    expect(hint.textContent).toBe("Enter 发送");
    expect(hint.closest('[data-testid="group-composer-card"]')).toBeNull();
  });

  it("lists group library documents in the + menu and toggles them", async () => {
    const onToggleAttachment = vi.fn();
    const onOpenLibrary = vi.fn();
    await act(async () =>
      root!.render(
        <AssistantGroupComposer
          value=""
          onChange={vi.fn()}
          onSend={vi.fn()}
          sending={false}
          placeholder="发消息"
          members={members}
          quoted={null}
          onClearQuote={vi.fn()}
          libraryDocuments={[
            {
              documentId: "d1",
              name: "方案.pdf",
              contentType: "application/pdf",
              sizeBytes: 1024,
              uploadedBy: "u1",
              uploadedByName: "我",
              createdAt: "2026-11-13T05:00:00Z",
              downloadUrl: "/download/d1",
            },
          ]}
          attachmentIds={["d1"]}
          onToggleAttachment={onToggleAttachment}
          onOpenLibrary={onOpenLibrary}
        />,
      ),
    );
    // 已选附件显示为可移除的 chip。
    expect(host.textContent).toContain("方案.pdf");
    await act(async () =>
      host.querySelector<HTMLButtonElement>('[aria-label="移除附件 方案.pdf"]')!.click(),
    );
    expect(onToggleAttachment).toHaveBeenCalledWith("d1");

    await act(async () =>
      host.querySelector<HTMLButtonElement>('[aria-label^="添加附件或知识库"]')!.click(),
    );
    const item = host.querySelector('[role="menuitemcheckbox"]')!;
    expect(item.getAttribute("aria-checked")).toBe("true");
    await act(async () => (item as HTMLButtonElement).click());
    expect(onToggleAttachment).toHaveBeenCalledTimes(2);

    await act(async () =>
      host.querySelector<HTMLButtonElement>('[role="menuitem"]')!.click(),
    );
    expect(onOpenLibrary).toHaveBeenCalledTimes(1);
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
