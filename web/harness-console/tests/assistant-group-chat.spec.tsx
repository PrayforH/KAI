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
  deleteAssistantGroup: vi.fn<() => Promise<void>>(async () => undefined),
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

/** 轮询会先过 allSettled 再落地状态，单个微任务不够。 */
async function flushPoll() {
  await act(async () => {
    for (let index = 0; index < 10; index += 1) await Promise.resolve();
  });
}

/** 走真实群信息面板：解散群聊 → 输入群名 → 确认解散。 */
async function startManualDissolve(confirmName = "产品评审群") {
  await act(async () => {
    host.querySelector<HTMLButtonElement>('[aria-label="群信息"]')!.click();
  });
  await act(async () => {
    [...host.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "解散群聊")!
      .click();
  });
  const input = host.querySelector<HTMLInputElement>('[aria-label="输入群名确认解散"]')!;
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  await act(async () => {
    setter.call(input, confirmName);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => {
    [...host.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "确认解散")!
      .click();
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

      // 描述来自同一份刷新后的群元数据，打开群信息就能看到。
      await act(async () => {
        host.querySelector<HTMLButtonElement>('[aria-label="群信息"]')!.click();
      });
      const description = host.querySelector<HTMLTextAreaElement>(
        'section[aria-label="群资料"] textarea',
      )!;
      expect(description.value).toBe("助理补充的描述");
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

  it("still dissolves from the info panel after the confirmation", async () => {
    const onDissolved = vi.fn();
    await act(async () =>
      root!.render(<AssistantGroupChat groupId="g1" userId="u1" onDissolved={onDissolved} />),
    );
    await flush();

    await act(async () => {
      host.querySelector<HTMLButtonElement>('[aria-label="群信息"]')!.click();
    });
    const dissolveButton = [...host.querySelectorAll<HTMLButtonElement>("button")].find(
      (button) => button.textContent === "解散群聊",
    )!;
    await act(async () => dissolveButton.click());

    const input = host.querySelector<HTMLInputElement>('[aria-label="输入群名确认解散"]')!;
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
    await act(async () => {
      setter.call(input, "产品评审群");
      input.dispatchEvent(new Event("input", { bubbles: true }));
    });
    const confirm = [...host.querySelectorAll<HTMLButtonElement>("button")].find(
      (button) => button.textContent === "确认解散",
    )!;
    await act(async () => confirm.click());
    await flush();

    expect(mocks.deleteAssistantGroup).toHaveBeenCalledWith("g1");
    expect(onDissolved).toHaveBeenCalledTimes(1);
    expect(host.querySelector("textarea")).toBeNull();
    expect(host.textContent).toContain("这个群聊已解散");
  });

  it("leaves once when polling confirms the dissolve before the manual response", async () => {
    vi.useFakeTimers();
    const onDissolved = vi.fn();
    await act(async () =>
      root!.render(
        <AssistantGroupChat key="g1" groupId="g1" userId="u1" onDissolved={onDissolved} />,
      ),
    );
    await flushPoll();

    // DELETE 已提交，但响应还在路上。
    let finishDelete: (() => void) | undefined;
    mocks.deleteAssistantGroup.mockReset();
    mocks.deleteAssistantGroup.mockImplementation(
      () => new Promise<void>((resolve) => { finishDelete = resolve; }),
    );
    await startManualDissolve();
    expect(mocks.deleteAssistantGroup).toHaveBeenCalledWith("g1");

    // 下一轮轮询先确认群已不存在，只离场一次。
    mocks.listAssistantGroups.mockResolvedValue([]);
    mocks.listAssistantGroupMessages.mockRejectedValue(
      new Error("操作未完成（404），请检查输入或稍后重试。"),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500);
    });
    await flushPoll();
    expect(onDissolved).toHaveBeenCalledTimes(1);

    // 用户已打开 g2，按生产页面的 key={groupParam} 重挂载。
    stateGroup = group2();
    mocks.listAssistantGroups.mockImplementation(async () => [stateGroup]);
    mocks.listAssistantGroupMessages.mockImplementation(async () => [message({ groupId: "g2" })]);
    await act(async () =>
      root!.render(
        <AssistantGroupChat key="g2" groupId="g2" userId="u1" onDissolved={onDissolved} />,
      ),
    );
    await flushPoll();
    expect(host.textContent).toContain("新的项目群");

    // g1 的 DELETE 这时才成功返回：不得再次导航，也不得清掉新群。
    await act(async () => { finishDelete?.(); });
    await flushPoll();
    expect(onDissolved).toHaveBeenCalledTimes(1);
    expect(host.textContent).toContain("新的项目群");
    expect(host.querySelector("textarea")).not.toBeNull();
  });

  it("leaves once when the manual dissolve finishes before polling", async () => {
    vi.useFakeTimers();
    const onDissolved = vi.fn();
    await act(async () =>
      root!.render(<AssistantGroupChat groupId="g1" userId="u1" onDissolved={onDissolved} />),
    );
    await flushPoll();

    mocks.deleteAssistantGroup.mockReset();
    mocks.deleteAssistantGroup.mockImplementation(async () => undefined);
    await startManualDissolve();
    await flushPoll();
    expect(onDissolved).toHaveBeenCalledTimes(1);
    expect(host.querySelector("textarea")).toBeNull();
    expect(host.textContent).toContain("这个群聊已解散");

    // 离场后轮询停止：服务端后续返回「群已删除」也不会再离场或再请求。
    mocks.listAssistantGroups.mockResolvedValue([]);
    mocks.listAssistantGroupMessages.mockRejectedValue(
      new Error("操作未完成（404），请检查输入或稍后重试。"),
    );
    const groupReads = mocks.listAssistantGroups.mock.calls.length;
    const messageReads = mocks.listAssistantGroupMessages.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10000);
    });
    await flushPoll();
    expect(onDissolved).toHaveBeenCalledTimes(1);
    expect(mocks.listAssistantGroups.mock.calls.length).toBe(groupReads);
    expect(mocks.listAssistantGroupMessages.mock.calls.length).toBe(messageReads);
  });

  it("ignores a late dissolve failure after switching to another group", async () => {
    vi.useFakeTimers();
    const onDissolved = vi.fn();
    await act(async () =>
      root!.render(
        <AssistantGroupChat key="g1" groupId="g1" userId="u1" onDissolved={onDissolved} />,
      ),
    );
    await flushPoll();

    let failDelete: ((reason: Error) => void) | undefined;
    mocks.deleteAssistantGroup.mockReset();
    mocks.deleteAssistantGroup.mockImplementation(
      () => new Promise<void>((_resolve, reject) => { failDelete = reject; }),
    );
    await startManualDissolve();

    stateGroup = group2();
    mocks.listAssistantGroups.mockImplementation(async () => [stateGroup]);
    mocks.listAssistantGroupMessages.mockImplementation(async () => [message({ groupId: "g2" })]);
    await act(async () =>
      root!.render(
        <AssistantGroupChat key="g2" groupId="g2" userId="u1" onDissolved={onDissolved} />,
      ),
    );
    await flushPoll();
    expect(host.textContent).toContain("新的项目群");

    // g1 的 DELETE 晚到失败：不能让新群显示旧错误，更不能离场。
    await act(async () => { failDelete?.(new Error("解散失败，请稍后重试。")); });
    await flushPoll();
    expect(onDissolved).not.toHaveBeenCalled();
    expect(host.textContent).toContain("新的项目群");
    expect(host.textContent).not.toContain("解散失败，请稍后重试。");
    expect(host.querySelector("textarea")).not.toBeNull();
  });

  it("does not leave when an unmounted group's dissolve response arrives", async () => {
    vi.useFakeTimers();
    const onDissolved = vi.fn();
    await act(async () =>
      root!.render(<AssistantGroupChat groupId="g1" userId="u1" onDissolved={onDissolved} />),
    );
    await flushPoll();

    let finishDelete: (() => void) | undefined;
    mocks.deleteAssistantGroup.mockReset();
    mocks.deleteAssistantGroup.mockImplementation(
      () => new Promise<void>((resolve) => { finishDelete = resolve; }),
    );
    await startManualDissolve();

    await act(async () => root!.unmount());
    root = undefined;

    await act(async () => { finishDelete?.(); });
    await flushPoll();
    expect(onDissolved).not.toHaveBeenCalled();
  });

  it("keeps the group page and allows a retry when the manual dissolve fails", async () => {
    vi.useFakeTimers();
    const onDissolved = vi.fn();
    await act(async () =>
      root!.render(<AssistantGroupChat groupId="g1" userId="u1" onDissolved={onDissolved} />),
    );
    await flushPoll();

    mocks.deleteAssistantGroup.mockReset();
    mocks.deleteAssistantGroup.mockRejectedValueOnce(new Error("解散失败，请稍后重试。"));
    await startManualDissolve();
    await flushPoll();

    // 失败不写离场标记：群页与输入框保留，错误可见。
    expect(onDissolved).not.toHaveBeenCalled();
    expect(host.querySelector("textarea")).not.toBeNull();
    expect(host.textContent).toContain("解散失败，请稍后重试。");

    // 用户可以直接重试；成功后只离场一次。
    mocks.deleteAssistantGroup.mockResolvedValue(undefined);
    await act(async () => {
      [...host.querySelectorAll<HTMLButtonElement>("button")]
        .find((button) => button.textContent === "确认解散")!
        .click();
    });
    await flushPoll();
    expect(mocks.deleteAssistantGroup).toHaveBeenCalledTimes(2);
    expect(onDissolved).toHaveBeenCalledTimes(1);
    expect(host.querySelector("textarea")).toBeNull();
    expect(host.textContent).toContain("这个群聊已解散");
  });
});
