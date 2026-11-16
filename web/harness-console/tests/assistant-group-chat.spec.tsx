// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AssistantGroup, GroupMessage } from "../src/lib/assistant-client";

const mocks = vi.hoisted(() => ({
  listAssistantGroups: vi.fn(),
  listAssistantGroupMessages: vi.fn(),
  assistantRequest: vi.fn(async () => []),
  markAssistantGroupRead: vi.fn(async () => undefined),
  postAssistantGroupMessage: vi.fn(),
  toggleAssistantGroupReaction: vi.fn(),
  updateAssistantGroup: vi.fn(),
  deleteAssistantGroup: vi.fn(async () => undefined),
  addAssistantGroupMember: vi.fn(),
  removeAssistantGroupMember: vi.fn(),
  setAssistantGroupReplyAs: vi.fn(),
  getAssistantGroupLibrary: vi.fn(),
  uploadAssistantGroupDocument: vi.fn(async () => undefined),
  deleteAssistantGroupDocument: vi.fn(async () => undefined),
  bindAssistantGroupKnowledgeBase: vi.fn(async () => undefined),
  unbindAssistantGroupKnowledgeBase: vi.fn(async () => undefined),
}));

vi.mock("../src/lib/assistant-client", () => mocks);
vi.mock("../src/lib/studio-client", () => ({
  studioClient: { listKnowledgeBases: vi.fn(async () => []) },
}));

import { AssistantGroupChat } from "../src/components/assistant-group-chat";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

let stateGroup: AssistantGroup;
let stateMessages: GroupMessage[];

function group(overrides: Partial<AssistantGroup> = {}): AssistantGroup {
  return {
    groupId: "g1",
    name: "产品评审群",
    creatorUserId: "u1",
    primaryPresent: true,
    replyAs: null,
    lastReadAt: null,
    unreadCount: 0,
    members: [
      { memberId: "u1", kind: "creator", name: "我", specialistId: null, status: "active", joinedAt: "2026-11-01T00:00:00Z" },
      { memberId: "primary", kind: "primary", name: "主助理", specialistId: null, status: "active", joinedAt: "2026-11-01T00:00:00Z" },
    ],
    createdAt: "2026-11-01T00:00:00Z",
    updatedAt: "2026-11-01T00:00:00Z",
    ...overrides,
  };
}

function message(overrides: Partial<GroupMessage> = {}): GroupMessage {
  return {
    messageId: "m1",
    groupId: "g1",
    author: "user",
    authorName: "我",
    specialistId: null,
    text: "我自己的消息",
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
  stateGroup = group();
  stateMessages = [message()];
  vi.clearAllMocks();
  mocks.listAssistantGroups.mockImplementation(async () => [{ ...stateGroup }]);
  mocks.listAssistantGroupMessages.mockImplementation(async () => [...stateMessages]);
  mocks.assistantRequest.mockResolvedValue([]);
  mocks.getAssistantGroupLibrary.mockResolvedValue({
    documents: [
      {
        documentId: "d1",
        name: "需求.docx",
        contentType: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        sizeBytes: 1024,
        uploadedBy: "u1",
        uploadedByName: "我",
        createdAt: "2026-11-13T05:00:00Z",
        downloadUrl: "/download/d1",
      },
    ],
    knowledgeBases: [],
  });
  mocks.updateAssistantGroup.mockImplementation(async (_groupId: string, fields: Partial<AssistantGroup>) => {
    stateGroup = { ...stateGroup, ...fields };
    return { ...stateGroup };
  });
  mocks.postAssistantGroupMessage.mockResolvedValue({ message: message(), replies: [], notices: [] });
});

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined;
  host.remove();
  window.localStorage.clear();
});

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe("AssistantGroupChat", () => {
  it("sends selected group library documents with the message", async () => {
    await act(async () =>
      root!.render(<AssistantGroupChat groupId="g1" userId="u1" />),
    );
    await flush();

    const input = host.querySelector<HTMLTextAreaElement>("textarea")!;
    const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value")!.set!;
    await act(async () => {
      setter.call(input, "带附件的消息");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });

    await act(async () => {
      host.querySelector<HTMLButtonElement>('[aria-label^="添加附件或知识库"]')!.click();
    });
    await act(async () => {
      host.querySelector<HTMLButtonElement>('[role="menuitemcheckbox"]')!.click();
    });
    await act(async () => {
      host.querySelector<HTMLButtonElement>('[aria-label="发送"]')!.click();
    });
    await flush();

    expect(mocks.postAssistantGroupMessage).toHaveBeenCalledWith(
      "g1",
      "带附件的消息",
      expect.stringContaining("web-"),
      null,
      ["d1"],
    );
  });

  it("keeps the message and offers no delete action", async () => {
    await act(async () =>
      root!.render(<AssistantGroupChat groupId="g1" userId="u1" />),
    );
    await flush();
    expect(host.querySelector('[aria-label="删除这条消息"]')).toBeNull();
    expect(host.textContent).toContain("我自己的消息");
  });

  it("renames the group from the info panel and updates the header", async () => {
    const onGroupName = vi.fn();
    await act(async () =>
      root!.render(<AssistantGroupChat groupId="g1" userId="u1" onGroupName={onGroupName} />),
    );
    await flush();
    await act(async () => {
      host.querySelector<HTMLButtonElement>('[aria-label="群信息"]')!.click();
    });
    const name = host.querySelector<HTMLInputElement>('[aria-label="群信息"] input')!;
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    await act(async () => {
      setter.call(name, "产品周会");
      name.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const save = [...host.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "保存")!;
    await act(async () => save.click());
    await flush();

    expect(mocks.updateAssistantGroup).toHaveBeenCalledWith("g1", {
      name: "产品周会",
      description: null,
    });
    expect(onGroupName).toHaveBeenLastCalledWith("产品周会");
  });

  it("restores the draft for the same user and group after remount", async () => {
    window.localStorage.setItem(
      "harness-group-composer-draft:v1:u1:g1",
      "上次没发完的草稿",
    );
    await act(async () =>
      root!.render(<AssistantGroupChat groupId="g1" userId="u1" />),
    );
    await flush();
    expect(host.querySelector<HTMLTextAreaElement>("textarea")!.value).toBe("上次没发完的草稿");
  });

  it("toggles the side panel and closes it without opening group info", async () => {
    await act(async () =>
      root!.render(<AssistantGroupChat groupId="g1" userId="u1" />),
    );
    await flush();

    // 头部入口带 aria-expanded；侧栏自己的关闭按钮没有，用它区分同名按钮。
    const headerToggle = () =>
      host.querySelector<HTMLButtonElement>('[aria-expanded][aria-label$="群右侧栏"]')!;
    const aside = () => host.querySelector('aside[aria-label="群右侧栏"]');

    await act(async () => headerToggle().click());
    await flush();
    expect(aside()).not.toBeNull();
    expect(headerToggle().getAttribute("aria-label")).toBe("关闭群右侧栏");

    // 再点一次回到关闭态。
    await act(async () => headerToggle().click());
    expect(aside()).toBeNull();
    expect(headerToggle().getAttribute("aria-label")).toBe("打开群右侧栏");

    // 侧栏自己的 × 只关侧栏，不能触发群信息面板。
    await act(async () => headerToggle().click());
    await flush();
    const close = host.querySelector<HTMLButtonElement>(
      'aside[aria-label="群右侧栏"] button[aria-label="关闭群右侧栏"]',
    )!;
    await act(async () => close.click());
    expect(aside()).toBeNull();
    expect(host.querySelector('[role="dialog"][aria-label="群信息"]')).toBeNull();
  });

  it("keeps both header actions in the fixed title band, outside the message scroll area", async () => {
    await act(async () =>
      root!.render(<AssistantGroupChat groupId="g1" userId="u1" />),
    );
    await flush();

    const band = host.querySelector<HTMLElement>('[data-testid="assistant-surface-title"]')!;
    const timeline = host.querySelector<HTMLElement>('[data-testid="group-message-timeline"]')!;
    const sideToggle = host.querySelector<HTMLButtonElement>(
      '[aria-label$="群右侧栏"][aria-expanded]',
    )!;
    const infoToggle = host.querySelector<HTMLButtonElement>('[aria-label="群信息"]')!;

    expect(band).not.toBeNull();
    expect(timeline).not.toBeNull();
    // 两个入口都在标题带里，且都不在滚动容器里。
    expect(band.contains(sideToggle)).toBe(true);
    expect(band.contains(infoToggle)).toBe(true);
    expect(timeline.contains(sideToggle)).toBe(false);
    expect(timeline.contains(infoToggle)).toBe(false);
    // 标题带是滚动容器的前一个兄弟：消息滚动时带子不动，按钮矩形与气泡矩形必然分离。
    expect(band.nextElementSibling).toBe(timeline);
    // 滚动容器里不再有任何「absolute/fixed 的头部悬浮控件」。
    expect(timeline.querySelector('[data-testid="assistant-surface-title"]')).toBeNull();
  });

  it("lays the title band out above a scrolling timeline at 1440 and 1024", () => {
    // jsdom 不做布局：这里守住几何不相交的布局前提——
    // 标题带 flex:0 0 auto 且不在 overflow 容器内；消息列 flex:1 + overflow:auto，
    // 其内容被裁在自己的矩形里。两档宽度共用同一套分栏，没有宽度分支。
    const css = readFileSync(
      resolve(process.cwd(), "src/components/assistant-group-chat.module.css"),
      "utf8",
    );
    const bandCss = readFileSync(
      resolve(process.cwd(), "src/app/assistant-conversation.css"),
      "utf8",
    );
    const mainBlock = css.match(/\.main\s*\{([^}]*)\}/)?.[1] ?? "";
    const timelineBlock = css.match(/\.timeline\s*\{([^}]*)\}/)?.[1] ?? "";
    const settingsBlock = css.match(/\.settings\s*\{([^}]*)\}/)?.[1] ?? "";
    const bandBlock =
      bandCss.match(/\.assistant-surface-title\s*\{([^}]*)\}/)?.[1] ?? "";

    expect(mainBlock).toContain("flex-direction: column");
    expect(bandBlock).toContain("flex: 0 0 auto");
    expect(bandBlock).toContain("min-height: 46px");
    expect(timelineBlock).toContain("flex: 1");
    expect(timelineBlock).toContain("overflow: auto");
    expect(timelineBlock).not.toContain("position: absolute");
    // 标题带里的按钮组不再绝对定位到滚动区上方。
    expect(settingsBlock).not.toContain("position: absolute");
    expect(settingsBlock).toContain("margin-left: auto");
    // 只有 900px 以下的横向内边距收窄，两档桌面宽度没有分栏变化。
    expect(css).not.toContain("@media (max-width: 1024px)");
    expect(css).not.toContain("@media (min-width: 1200px)");
  });
});
