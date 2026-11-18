// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { GroupMessage } from "../src/lib/assistant-client";

const mocks = vi.hoisted(() => ({
  listAssistantGroupMessages: vi.fn(),
}));

vi.mock("../src/lib/assistant-client", () => mocks);

import { AssistantGroupHistoryPanel } from "../src/components/assistant-group-history-panel";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | undefined;
let host: HTMLDivElement;

function message(overrides: Partial<GroupMessage> = {}): GroupMessage {
  return {
    messageId: "m1",
    groupId: "g1",
    author: "primary",
    authorName: "主助理",
    specialistId: null,
    text: "已完成评审",
    runId: null,
    replyToMessageId: null,
    reactions: [],
    status: "completed",
    createdAt: "2026-11-13T05:00:00Z",
    ...overrides,
  };
}

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  vi.clearAllMocks();
});

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined;
  host.replaceChildren();
});

async function flush() {
  await act(async () => {
    await new Promise((done) => setTimeout(done, 0));
    await new Promise((done) => setTimeout(done, 0));
  });
}

describe("AssistantGroupHistoryPanel", () => {
  it("lists the group's messages from the group-level history endpoint", async () => {
    mocks.listAssistantGroupMessages.mockResolvedValue([
      message({ messageId: "m1", text: "第一句" }),
      message({ messageId: "m2", author: "user", authorName: "我", text: "第二句" }),
    ]);
    await act(async () =>
      root!.render(<AssistantGroupHistoryPanel open groupId="g1" onClose={() => {}} />),
    );
    await flush();

    expect(mocks.listAssistantGroupMessages).toHaveBeenCalledWith("g1");
    expect(host.textContent).toContain("聊天记录");
    expect(host.textContent).toContain("第一句");
    expect(host.textContent).toContain("第二句");
    expect(host.textContent).toContain("我");
    // 最新一条在上。
    const text = host.textContent ?? "";
    expect(text.indexOf("第二句")).toBeLessThan(text.indexOf("第一句"));
  });

  it("renders a status payload as readable text instead of raw JSON", async () => {
    mocks.listAssistantGroupMessages.mockResolvedValue([
      message({ messageId: "m1", author: "specialist", authorName: "架构评审官", text: '{"status":"ready"}' }),
    ]);
    await act(async () =>
      root!.render(<AssistantGroupHistoryPanel open groupId="g1" onClose={() => {}} />),
    );
    await flush();

    expect(host.textContent).not.toContain('{"status":"ready"}');
    expect(host.textContent).toContain("已就绪");
  });

  it("locates a message back in the group chat through one event", async () => {
    mocks.listAssistantGroupMessages.mockResolvedValue([
      message({ messageId: "m1", text: "第一句" }),
    ]);
    const seen: unknown[] = [];
    const listen = (event: Event) => seen.push((event as CustomEvent).detail);
    window.addEventListener("kai:group-locate", listen);
    try {
      await act(async () =>
        root!.render(<AssistantGroupHistoryPanel open groupId="g1" onClose={() => {}} />),
      );
      await flush();
      const locate = [...host.querySelectorAll<HTMLButtonElement>("button")]
        .find((button) => button.textContent === "在聊天中定位")!;
      await act(async () => locate.click());
      expect(seen).toEqual([{ groupId: "g1", messageId: "m1" }]);
    } finally {
      window.removeEventListener("kai:group-locate", listen);
    }
  });

  it("keeps an error visible and offers a retry through the shared list", async () => {
    mocks.listAssistantGroupMessages.mockRejectedValue(new Error("聊天记录暂时无法读取"));
    await act(async () =>
      root!.render(<AssistantGroupHistoryPanel open groupId="g1" onClose={() => {}} />),
    );
    await flush();
    expect(host.querySelector('[role="alert"]')!.textContent).toContain("聊天记录暂时无法读取");
  });

  it("does not fetch while closed", async () => {
    await act(async () =>
      root!.render(<AssistantGroupHistoryPanel open={false} groupId="g1" onClose={() => {}} />),
    );
    expect(mocks.listAssistantGroupMessages).not.toHaveBeenCalled();
    expect(host.querySelector('aside[aria-label="群聊天记录"]')).toBeNull();
  });
});
