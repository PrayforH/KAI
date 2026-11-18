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

describe("群聊天记录的文件分类与筛选", () => {
  const withFiles = () => [
    message({
      messageId: "m1",
      author: "user",
      authorName: "我",
      text: "这是资料",
      createdAt: "2026-11-13T05:00:00Z",
      attachments: [
        { documentId: "d1", name: "封面.png", contentType: "image/png", sizeBytes: 2048, downloadUrl: "/v1/studio/assistant/groups/g1/library/documents/d1/content" },
        { documentId: "d2", name: "评审.pdf", contentType: "application/pdf", sizeBytes: 4096, downloadUrl: "/v1/studio/assistant/groups/g1/library/documents/d2/content" },
      ],
    }),
    message({ messageId: "m2", text: "收到", createdAt: "2026-11-13T06:00:00Z" }),
  ];

  it("has the same 消息/文件/图片与视频 tabs as the single assistant", async () => {
    mocks.listAssistantGroupMessages.mockResolvedValue(withFiles());
    await act(async () => root!.render(<AssistantGroupHistoryPanel open groupId="g1" onClose={() => {}} />));
    await flush();

    const tabs = [...host.querySelectorAll<HTMLButtonElement>('[role="tab"]')].map(tab => tab.textContent);
    expect(tabs).toEqual(["全部", "文件", "图片与视频"]);
  });

  it("classifies attachments by kind and filters by type", async () => {
    mocks.listAssistantGroupMessages.mockResolvedValue(withFiles());
    await act(async () => root!.render(<AssistantGroupHistoryPanel open groupId="g1" onClose={() => {}} />));
    await flush();

    const filesTab = [...host.querySelectorAll<HTMLButtonElement>('[role="tab"]')]
      .find(tab => tab.textContent === "文件")!;
    await act(async () => filesTab.click());

    expect(host.textContent).toContain("封面.png");
    expect(host.textContent).toContain("评审.pdf");
    const kind = host.querySelector<HTMLSelectElement>('[aria-label="按文件类型筛选"]')!;
    const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value")!.set!;
    await act(async () => {
      setter.call(kind, "pdf");
      kind.dispatchEvent(new Event("change", { bubbles: true }));
    });
    expect(host.textContent).toContain("评审.pdf");
    expect(host.textContent).not.toContain("封面.png");
  });

  it("shows image attachments in the media grid with an inline href", async () => {
    mocks.listAssistantGroupMessages.mockResolvedValue(withFiles());
    await act(async () => root!.render(<AssistantGroupHistoryPanel open groupId="g1" onClose={() => {}} />));
    await flush();

    const mediaTab = [...host.querySelectorAll<HTMLButtonElement>('[role="tab"]')]
      .find(tab => tab.textContent === "图片与视频")!;
    await act(async () => mediaTab.click());

    const img = host.querySelector("img")!;
    expect(img.getAttribute("src")).toBe("/api/studio/assistant/groups/g1/library/documents/d1/content?inline=1");
    expect(host.querySelectorAll("figure")).toHaveLength(1);
  });

  it("opens group documents through their library download link", async () => {
    mocks.listAssistantGroupMessages.mockResolvedValue(withFiles());
    await act(async () => root!.render(<AssistantGroupHistoryPanel open groupId="g1" onClose={() => {}} />));
    await flush();

    const filesTab = [...host.querySelectorAll<HTMLButtonElement>('[role="tab"]')]
      .find(tab => tab.textContent === "文件")!;
    await act(async () => filesTab.click());
    const links = [...host.querySelectorAll<HTMLAnchorElement>("a")].map(link => link.getAttribute("href"));
    expect(links).toContain("/api/studio/assistant/groups/g1/library/documents/d1/content?inline=1");
    expect(links).toContain("/api/studio/assistant/groups/g1/library/documents/d2/content");
  });
});
