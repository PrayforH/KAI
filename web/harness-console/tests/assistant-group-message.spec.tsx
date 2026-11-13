// @vitest-environment jsdom
import { act, type ReactNode } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AssistantGroupMessage, GROUP_REACTIONS } from "../src/components/assistant-group-message";
import type { GroupMessage } from "../src/lib/assistant-client";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function message(overrides: Partial<GroupMessage> = {}): GroupMessage {
  return {
    messageId: "m1",
    groupId: "g1",
    author: "specialist",
    authorName: "Agent 内核",
    specialistId: "kernel",
    text: "收到",
    runId: null,
    replyToMessageId: null,
    reactions: [],
    status: "completed",
    createdAt: "2026-11-13T05:00:00Z",
    ...overrides,
  };
}

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

async function render(node: ReactNode) {
  await act(async () => root!.render(node));
}

describe("AssistantGroupMessage", () => {
  it("renders bot markdown without a bubble", async () => {
    await render(
      <AssistantGroupMessage
        message={message({
          text: "## 结论\n\n- 第一点\n- 第二点\n\n| 项 | 值 |\n|---|---|\n| A | 1 |\n\n```ts\nconst a = 1;\n```",
        })}
        tone="#38d591"
        onReact={vi.fn()}
        onReply={vi.fn()}
      />,
    );
    expect(host.querySelector("h1, h2, h3")?.textContent).toContain("结论");
    expect(host.querySelectorAll("li")).toHaveLength(2);
    expect(host.querySelector("table")).not.toBeNull();
    expect(host.querySelector("pre code")?.textContent).toContain("const a = 1");
  });

  it("renders a mention as a chip", async () => {
    await render(
      <AssistantGroupMessage
        message={message({ text: "请 @产品规划 看一下" })}
        tone="#38d591"
        onReact={vi.fn()}
        onReply={vi.fn()}
      />,
    );
    const chip = host.querySelector("[data-mention]");
    expect(chip?.textContent).toBe("@产品规划");
  });

  it("keeps user messages on the right as a bubble", async () => {
    await render(
      <AssistantGroupMessage
        message={message({ author: "user", authorName: "我", text: "**加粗**" })}
        tone="#6b746e"
        onReact={vi.fn()}
        onReply={vi.fn()}
      />,
    );
    const article = host.querySelector('[data-author="user"]');
    expect(article).not.toBeNull();
    expect(article?.querySelector("strong")?.textContent).toBe("加粗");
  });

  it("renders system notices centred and without actions", async () => {
    await render(
      <AssistantGroupMessage
        message={message({ author: "system", authorName: "系统", text: "成员已加入" })}
        tone="#6b746e"
        onReact={vi.fn()}
        onReply={vi.fn()}
      />,
    );
    const article = host.querySelector('[data-author="system"]');
    expect(article?.textContent).toContain("成员已加入");
    expect(article?.querySelector("button")).toBeNull();
  });

  it("shows the running placeholder for an empty reply", async () => {
    await render(
      <AssistantGroupMessage
        message={message({ text: "", status: "running" })}
        tone="#38d591"
        onReact={vi.fn()}
        onReply={vi.fn()}
      />,
    );
    expect(host.textContent).toContain("正在回复…");
  });

  it("renders the quote snapshot when replyTo is present", async () => {
    await render(
      <AssistantGroupMessage
        message={message({
          replyToMessageId: "m0",
          replyTo: {
            messageId: "m0",
            author: "user",
            authorName: "我",
            text: "上一句问题",
          },
        })}
        tone="#38d591"
        onReact={vi.fn()}
        onReply={vi.fn()}
      />,
    );
    expect(host.querySelector('[aria-label="引用 我"]')?.textContent).toContain("上一句问题");
  });

  it("renders no quote block when replyTo is omitted", async () => {
    await render(
      <AssistantGroupMessage
        message={message({ replyToMessageId: null })}
        tone="#38d591"
        onReact={vi.fn()}
        onReply={vi.fn()}
      />,
    );
    expect(host.querySelector('[aria-label^="引用 "]')).toBeNull();
  });

  it("opens the five-key picker and reports a reaction", async () => {
    const onReact = vi.fn();
    await render(
      <AssistantGroupMessage
        message={message()}
        tone="#38d591"
        onReact={onReact}
        onReply={vi.fn()}
      />,
    );
    const trigger = host.querySelector<HTMLButtonElement>('[aria-label="给这条消息加表情"]')!;
    await act(async () => trigger.click());
    const picker = host.querySelector('[aria-label="选择表情"]')!;
    expect(picker.querySelectorAll("button")).toHaveLength(GROUP_REACTIONS.length);
    await act(async () =>
      picker.querySelector<HTMLButtonElement>("button")!.click(),
    );
    expect(onReact).toHaveBeenCalledWith(expect.objectContaining({ messageId: "m1" }), GROUP_REACTIONS[0]);
  });

  it("cancels a selected reaction chip", async () => {
    const onReact = vi.fn();
    await render(
      <AssistantGroupMessage
        message={message({ reactions: ["👍"] })}
        tone="#38d591"
        onReact={onReact}
        onReply={vi.fn()}
      />,
    );
    const chip = host.querySelector<HTMLButtonElement>('[aria-pressed="true"]')!;
    expect(chip.textContent).toBe("👍");
    await act(async () => chip.click());
    expect(onReact).toHaveBeenCalledWith(expect.anything(), "👍");
  });

  it("reports a reply request", async () => {
    const onReply = vi.fn();
    await render(
      <AssistantGroupMessage
        message={message()}
        tone="#38d591"
        onReact={vi.fn()}
        onReply={onReply}
      />,
    );
    await act(async () =>
      host.querySelector<HTMLButtonElement>('[aria-label="引用这条消息"]')!.click(),
    );
    expect(onReply).toHaveBeenCalledWith(expect.objectContaining({ messageId: "m1" }));
  });
});
