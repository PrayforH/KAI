// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/link", () => ({
  default: ({ children, ...props }: { children?: unknown }) => <a {...props}>{children as never}</a>,
}));

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
    });
    await act(async () => {
      root!.render(<AssistantMentionReceipts />);
    });
    const line = host.querySelector(".assistant-event-line");
    expect(line?.textContent).toContain("已发送消息给");
    expect(line?.textContent).toContain("资料 Bot");
    // 点了名字直接去那个 Bot 的会话。
    expect(host.querySelector("a")?.getAttribute("href")).toBe("/assistant?bot=specialist_a");
  });
});
