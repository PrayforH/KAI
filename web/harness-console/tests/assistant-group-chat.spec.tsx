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
  addAssistantGroupMember: vi.fn(),
  removeAssistantGroupMember: vi.fn(),
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

  it("has no reply-as control; un-@ mentions are left to the primary assistant", async () => {
    // 老群即使服务端还留着历史接话人，前端也不提供设置入口、不写入 replyAs。
    stateGroup = group({ replyAs: "specialist-old" });
    await act(async () =>
      root!.render(<AssistantGroupChat groupId="g1" userId="u1" />),
    );
    await flush();

    expect(host.textContent).not.toContain("接话人");
    expect(host.textContent).not.toContain("未 @");
    expect(host.querySelector('[aria-label*="接话人"]')).toBeNull();
    expect(host.querySelector('[role="dialog"][aria-label*="接话人"]')).toBeNull();
    // 前端只在群资料 PATCH 里写 name/description，任何一次调用都不带 replyAs。
    for (const call of mocks.assistantRequest.mock.calls as unknown[][]) {
      const body = call[2] as Record<string, unknown> | undefined;
      expect(body ?? {}).not.toHaveProperty("replyAs");
    }
  });

  it("opens the group info sheet from the title band's group name", async () => {
    stateGroup = group({ description: "KAI 项目研发协作组。" });
    await act(async () =>
      root!.render(<AssistantGroupChat groupId="g1" userId="u1" />),
    );
    await flush();

    const band = host.querySelector<HTMLElement>('[data-testid="assistant-surface-title"]')!;
    // 旧的「群信息」按钮和 ⋯ 菜单都不在了：改名/改说明在左侧列表的 ⋯ 菜单里。
    expect(band.querySelector('[aria-label="群信息"]')).toBeNull();
    expect(band.textContent).not.toContain("⋯");

    // 和单助理一样，点群名看群资料：谁回复 / 说明 / 成员。
    const nameButton = band.querySelector<HTMLButtonElement>('[aria-label$="群资料"]')!;
    expect(nameButton.textContent).toBe("产品评审群");
    await act(async () => nameButton.click());
    const dialog = host.querySelector<HTMLElement>('[role="dialog"][aria-modal="true"]')!;
    expect(dialog).not.toBeNull();
    expect(dialog.textContent).toContain("谁回复");
    expect(dialog.textContent).toContain("主助理");
    expect(dialog.textContent).toContain("未 @ 时由它接话");
    expect(dialog.textContent).toContain("KAI 项目研发协作组。");
    expect(dialog.textContent).toContain("2 人");

    // 成员行是跳转：关掉资料，打开成员管理。
    const members = [...dialog.querySelectorAll("button")].find((button) => button.textContent?.includes("人"))!;
    await act(async () => members.click());
    await flush();
    expect(host.querySelector('[role="dialog"]')?.textContent).toContain("成员管理");

    await act(async () => {
      host.querySelector<HTMLButtonElement>('[aria-expanded][aria-label$="群右侧栏"]')!.click();
    });
    await flush();
    expect(host.querySelector('aside[aria-label="群右侧栏"]')!.textContent).not.toContain("群信息");
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

  it("toggles the side panel from the title band", async () => {
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

    // 侧栏自己的 × 只关侧栏；群管理入口只有标题带的头像簇。
    await act(async () => headerToggle().click());
    await flush();
    const close = host.querySelector<HTMLButtonElement>(
      'aside[aria-label="群右侧栏"] button[aria-label="关闭群右侧栏"]',
    )!;
    await act(async () => close.click());
    expect(aside()).toBeNull();
    expect(host.querySelector('[role="dialog"][aria-label="群信息"]')).toBeNull();
  });

  it("opens the chat history from its own title-band button", async () => {
    const onToggleHistory = vi.fn();
    await act(async () =>
      root!.render(
        <AssistantGroupChat
          groupId="g1"
          userId="u1"
          historyOpen={false}
          onToggleHistory={onToggleHistory}
        />,
      ),
    );
    await flush();

    const band = host.querySelector<HTMLElement>('[data-testid="assistant-surface-title"]')!;
    const historyToggle = host.querySelector<HTMLButtonElement>(
      '[aria-label$="的聊天记录"][aria-expanded]',
    )!;
    expect(historyToggle).not.toBeNull();
    expect(historyToggle.title).toBe("聊天记录");
    expect(historyToggle.getAttribute("aria-expanded")).toBe("false");
    expect(band.contains(historyToggle)).toBe(true);
    await act(async () => historyToggle.click());
    expect(onToggleHistory).toHaveBeenCalledTimes(1);

    // 聊天记录不再占用右侧栏的 tab：只剩动态 / 任务 / 资料库。
    const sideToggle = host.querySelector<HTMLButtonElement>('[aria-label$="群右侧栏"]')!;
    await act(async () => sideToggle.click());
    await flush();
    const tabs = [...host.querySelectorAll('[role="tab"]')].map((tab) => tab.textContent);
    expect(tabs).toEqual(["动态", "任务", "资料库"]);
    expect(host.querySelector('aside[aria-label="群右侧栏"]')!.textContent).not.toContain("聊天记录");
  });

  it("keeps every header action in the fixed title band, outside the message scroll area", async () => {
    await act(async () =>
      root!.render(<AssistantGroupChat groupId="g1" userId="u1" />),
    );
    await flush();

    const band = host.querySelector<HTMLElement>('[data-testid="assistant-surface-title"]')!;
    const timeline = host.querySelector<HTMLElement>('[data-testid="group-message-timeline"]')!;
    const sideToggle = host.querySelector<HTMLButtonElement>(
      '[aria-label$="群右侧栏"][aria-expanded]',
    )!;
    const historyToggle = host.querySelector<HTMLButtonElement>(
      '[aria-label$="的聊天记录"][aria-expanded]',
    )!;
    const membersToggle = host.querySelector<HTMLButtonElement>(".group-member-faces")!;

    expect(band).not.toBeNull();
    expect(timeline).not.toBeNull();
    // 三个入口都在标题带里，且都不在滚动容器里。
    expect(band.contains(sideToggle)).toBe(true);
    expect(band.contains(historyToggle)).toBe(true);
    expect(band.contains(membersToggle)).toBe(true);
    expect(timeline.contains(sideToggle)).toBe(false);
    expect(timeline.contains(historyToggle)).toBe(false);
    expect(timeline.contains(membersToggle)).toBe(false);
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

/** 轮询会先过 allSettled 再落地状态，单个微任务不够。 */
async function flushPoll() {
  await act(async () => {
    for (let index = 0; index < 10; index += 1) await Promise.resolve();
  });
}

function group2() {
  return group({ groupId: "g2", name: "新的项目群" });
}

describe("AssistantGroupChat: server-side group admin sync", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("syncs a group renamed or redescribed by an assistant tool", async () => {
    vi.useFakeTimers();
    const onGroupName = vi.fn();
    const groupsChanged = vi.fn();
    window.addEventListener("kai:groups-changed", groupsChanged);
    try {
      await act(async () =>
        root!.render(<AssistantGroupChat groupId="g1" userId="u1" onGroupName={onGroupName} />),
      );
      await flushPoll();
      expect(onGroupName).toHaveBeenLastCalledWith("产品评审群");
      groupsChanged.mockClear();

      // 助理在服务端工具里改名并补了描述：页面下一轮轮询应自行跟上。
      stateGroup = { ...stateGroup, name: "助理改的新群名", description: "助理补充的描述" };
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3000);
      });
      await flushPoll();

      expect(onGroupName).toHaveBeenLastCalledWith("助理改的新群名");
      expect(host.querySelector(".task-context-title")?.textContent).toBe("助理改的新群名");
      expect(groupsChanged).toHaveBeenCalled();
      // 群管理面板已删除：同步只体现在标题带与左侧栏，不再有可打开的群资料表单。
      expect(host.querySelector('[aria-label="群信息"]')).toBeNull();
    } finally {
      window.removeEventListener("kai:groups-changed", groupsChanged);
    }
  });

  it("leaves the page and stops polling after an assistant tool dissolves the group", async () => {
    vi.useFakeTimers();
    const onDissolved = vi.fn();
    await act(async () =>
      root!.render(<AssistantGroupChat groupId="g1" userId="u1" onDissolved={onDissolved} />),
    );
    await flushPoll();
    expect(host.querySelector("textarea")).not.toBeNull();

    mocks.listAssistantGroups.mockResolvedValue([]);
    mocks.listAssistantGroupMessages.mockRejectedValue(
      new Error("操作未完成（404），请检查输入或稍后重试。"),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    await flushPoll();

    expect(onDissolved).toHaveBeenCalledTimes(1);
    expect(host.querySelector("textarea")).toBeNull();
    expect(host.textContent).toContain("这个群聊已解散");

    // 离场后不再请求这个群，也不会重复离场。
    const messageReads = mocks.listAssistantGroupMessages.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    expect(mocks.listAssistantGroupMessages.mock.calls.length).toBe(messageReads);
    expect(onDissolved).toHaveBeenCalledTimes(1);
  });

  it("keeps the group on network failures and recovers when the server returns", async () => {
    vi.useFakeTimers();
    const onDissolved = vi.fn();
    await act(async () =>
      root!.render(<AssistantGroupChat groupId="g1" userId="u1" onDissolved={onDissolved} />),
    );
    await flushPoll();

    const offline = new Error("操作未完成（503），请检查输入或稍后重试。");
    mocks.listAssistantGroups.mockRejectedValue(offline);
    mocks.listAssistantGroupMessages.mockRejectedValue(offline);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    await flushPoll();

    expect(onDissolved).not.toHaveBeenCalled();
    expect(host.querySelector("textarea")).not.toBeNull();
    expect(host.textContent).toContain("我自己的消息");

    mocks.listAssistantGroups.mockImplementation(async () => [{ ...stateGroup }]);
    mocks.listAssistantGroupMessages.mockImplementation(async () => [...stateMessages]);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    await flushPoll();

    expect(onDissolved).not.toHaveBeenCalled();
    expect(host.querySelector("textarea")).not.toBeNull();
    expect(host.textContent).toContain("我自己的消息");
  });

  it("does not dissolve while the message route 404s but the group still exists", async () => {
    vi.useFakeTimers();
    const onDissolved = vi.fn();
    await act(async () =>
      root!.render(<AssistantGroupChat groupId="g1" userId="u1" onDissolved={onDissolved} />),
    );
    await flushPoll();

    mocks.listAssistantGroupMessages.mockRejectedValue(
      new Error("操作未完成（404），请检查输入或稍后重试。"),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000);
    });
    await flushPoll();
    expect(onDissolved).not.toHaveBeenCalled();
    expect(host.querySelector("textarea")).not.toBeNull();

    mocks.listAssistantGroupMessages.mockImplementation(async () => [...stateMessages]);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(6000);
    });
    await flushPoll();
    expect(onDissolved).not.toHaveBeenCalled();
    expect(host.textContent).toContain("我自己的消息");
  });

  it("ignores a late 404 from the previous group after switching", async () => {
    vi.useFakeTimers();
    const onDissolved = vi.fn();
    await act(async () =>
      root!.render(<AssistantGroupChat groupId="g1" userId="u1" onDissolved={onDissolved} />),
    );
    await flushPoll();

    // g1 的下一轮消息请求卡住不返回，模拟切群后才到的旧响应。
    let rejectOld: ((reason: Error) => void) | undefined;
    mocks.listAssistantGroupMessages.mockImplementation((target: string) =>
      target === "g1"
        ? new Promise<GroupMessage[]>((_resolve, reject) => {
            rejectOld = reject;
          })
        : Promise.resolve([...stateMessages]),
    );
    await act(async () => {
      vi.advanceTimersByTime(2500);
      await Promise.resolve();
    });
    await flushPoll();

    stateGroup = group({ groupId: "g2", name: "新群" });
    await act(async () =>
      root!.render(<AssistantGroupChat groupId="g2" userId="u1" onDissolved={onDissolved} />),
    );
    await flushPoll();
    expect(host.textContent).toContain("新群");

    await act(async () => {
      rejectOld?.(new Error("操作未完成（404），请检查输入或稍后重试。"));
    });
    await flushPoll();

    expect(onDissolved).not.toHaveBeenCalled();
    expect(host.querySelector("textarea")).not.toBeNull();
    expect(host.textContent).toContain("新群");
  });
});
