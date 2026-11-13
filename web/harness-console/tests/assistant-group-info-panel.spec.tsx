// @vitest-environment jsdom
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AssistantGroupInfoPanel } from "../src/components/assistant-group-info-panel";
import type { AssistantGroup } from "../src/lib/assistant-client";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

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

function setValue(element: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const prototype = element instanceof HTMLTextAreaElement
    ? HTMLTextAreaElement.prototype
    : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(prototype, "value")!.set!;
  return act(async () => {
    setter.call(element, value);
    element.dispatchEvent(new Event("input", { bubbles: true }));
  });
}

function render(element: ReactElement) {
  return act(async () => root!.render(element));
}

function baseProps() {
  return {
    group: group(),
    isCreator: true,
    busy: false,
    error: null,
    specialists: [],
    onUpdate: vi.fn(async (): Promise<string | null> => null),
    onAddMember: vi.fn(),
    onRemoveMember: vi.fn(),
    onReplyAs: vi.fn(),
    onOpenLibrary: vi.fn(),
    onDissolve: vi.fn(async () => true),
    onClose: vi.fn(),
  };
}

describe("AssistantGroupInfoPanel", () => {
  it("renames and clears an empty description", async () => {
    const props = baseProps();
    await render(<AssistantGroupInfoPanel {...props} />);
    const name = host.querySelector<HTMLInputElement>('[aria-label="群信息"] input')!;
    expect(name.value).toBe("产品评审群");
    await setValue(name, "产品周会");
    const save = [...host.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "保存")!;
    await act(async () => save.click());
    expect(props.onUpdate).toHaveBeenCalledWith({ name: "产品周会", description: null });
  });

  it("shows the server message and keeps the input when saving fails", async () => {
    const props = baseProps();
    props.onUpdate.mockResolvedValue("群聊名称不能超过 40 个字");
    await render(<AssistantGroupInfoPanel {...props} />);
    const name = host.querySelector<HTMLInputElement>('[aria-label="群信息"] input')!;
    await setValue(name, "新名字");
    const save = [...host.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "保存")!;
    await act(async () => save.click());
    expect(name.value).toBe("新名字");
    expect(host.textContent).toContain("群聊名称不能超过 40 个字");
    expect(host.textContent).not.toContain("保存失败，已保留你输入的内容");
  });

  it("falls back to a generic message when the failure carries no text", async () => {
    const props = baseProps();
    props.onUpdate.mockResolvedValue("");
    await render(<AssistantGroupInfoPanel {...props} />);
    const name = host.querySelector<HTMLInputElement>('[aria-label="群信息"] input')!;
    await setValue(name, "新名字");
    const save = [...host.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "保存")!;
    await act(async () => save.click());
    expect(name.value).toBe("新名字");
    expect(host.textContent).toContain("保存失败，已保留你输入的内容");
  });

  it("rejects a name longer than the server's 40-character limit locally", async () => {
    const props = baseProps();
    await render(<AssistantGroupInfoPanel {...props} />);
    const name = host.querySelector<HTMLInputElement>('[aria-label="群信息"] input')!;
    expect(name.maxLength).toBe(40);
    await setValue(name, "群".repeat(41));
    const save = [...host.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "保存")!;
    await act(async () => save.click());
    expect(props.onUpdate).not.toHaveBeenCalled();
    expect(name.value).toBe("群".repeat(41));
    expect(host.textContent).toContain("群名最多 40 个字");
  });

  it("locks profile fields and hides dissolve for non-creators", async () => {
    await render(<AssistantGroupInfoPanel {...baseProps()} isCreator={false} />);
    expect(host.querySelector<HTMLInputElement>('[aria-label="群信息"] input')!.disabled).toBe(true);
    expect([...host.querySelectorAll("button")].some((button) => button.textContent === "保存")).toBe(false);
    expect(host.textContent).not.toContain("解散群聊");
  });

  it("dissolves only after typing the exact group name", async () => {
    const props = baseProps();
    await render(<AssistantGroupInfoPanel {...props} />);
    const trigger = [...host.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "解散群聊")!;
    await act(async () => trigger.click());
    const confirm = [...host.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "确认解散")!;
    expect(confirm.disabled).toBe(true);
    const input = host.querySelector<HTMLInputElement>('[aria-label="输入群名确认解散"]')!;
    await setValue(input, "错的群名");
    expect(confirm.disabled).toBe(true);
    await setValue(input, "产品评审群");
    expect(confirm.disabled).toBe(false);
    await act(async () => confirm.click());
    expect(props.onDissolve).toHaveBeenCalledTimes(1);
  });

  it("opens the library panel from the info panel", async () => {
    const props = baseProps();
    await render(<AssistantGroupInfoPanel {...props} />);
    const open = [...host.querySelectorAll<HTMLButtonElement>("button")]
      .find((button) => button.textContent === "打开资料库")!;
    await act(async () => open.click());
    expect(props.onOpenLibrary).toHaveBeenCalledTimes(1);
  });
});
