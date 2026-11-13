// @vitest-environment jsdom
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
  deleteAssistantGroupMessage: vi.fn(async () => undefined),
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

  it("deletes a message after confirmation and removes it locally", async () => {
    await act(async () =>
      root!.render(<AssistantGroupChat groupId="g1" userId="u1" />),
    );
    await flush();
    await act(async () => {
      host.querySelector<HTMLButtonElement>('[aria-label="删除这条消息"]')!.click();
    });
    await act(async () => {
      host.querySelector<HTMLButtonElement>('[aria-label="确认删除这条消息"]')!.click();
    });
    await flush();
    expect(mocks.deleteAssistantGroupMessage).toHaveBeenCalledWith("g1", "m1");
    expect(host.textContent).not.toContain("我自己的消息");
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
});
