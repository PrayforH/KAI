// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/link", () => ({
  default: ({ children, ...props }: { children?: unknown }) => <a {...props}>{children as never}</a>,
}));

vi.mock("../src/lib/mention-completion", () => ({ MENTION_RESULT_EVENT: "mention-result", watchMentionCompletion: async () => false }));
vi.mock("../src/lib/assistant-client", () => ({ assistantRequest: async () => [] }));

import { AssistantMentionReceipts } from "../src/components/assistant-mention-bar";
import { handoverStore } from "../src/lib/handover-store";

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

describe("mention receipts", () => {
  it("renders a durable receipt line for a sent mention", async () => {
    handoverStore.ingestMention({
      handoverId: "handover_mention_1",
      specialistId: "specialist_a",
      specialistName: "资料 Bot",
      sourceThreadId: "main",
      runId: "actual-run",
    });
    await act(async () => {
      root!.render(<AssistantMentionReceipts threadId="main" />);
    });
    const line = host.querySelector(".assistant-event-line");
    expect(line?.textContent).toContain("已发送消息给");
    expect(line?.textContent).toContain("资料 Bot");
    // 点了名字直接去那个 Bot 的会话。
    expect(host.querySelector("a")?.getAttribute("href")).toBe("/assistant?bot=specialist_a");
  });
});

it("never renders another conversation's receipts or ambiguous legacy receipts", async () => {
  handoverStore.ingestMention({ handoverId: "unscoped", specialistId: "old", specialistName: "旧回执" });
  handoverStore.ingestMention({ handoverId: "other", specialistId: "weather", specialistName: "天气提醒", sourceThreadId: "main", runId: "other-run" });
  await act(async () => { root!.render(<AssistantMentionReceipts threadId="rest" />); });
  expect(host.textContent).toBe("");
});

describe("回执的位置", () => {
  const thread = readFileSync(join(process.cwd(), "src/components/agent-thread.tsx"), "utf8");

  it("renders inside the conversation stream, not above the composer", () => {
    // MessagesFooter 在滚动区里（Thread.Messages 之后），Composer 在它之外；
    // 回执贴在输入框上方会看起来像"发送到"工具栏。
    expect(thread).toContain("MessagesFooter: HarnessMessagesFooter");
    expect(thread).toContain("return <AssistantMentionReceipts threadId={");
    // 输入区上方不再挂回执。
    expect(thread).not.toContain("{assistantSurface && <AssistantMentionReceipts");
  });
});
