// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { AssistantGroup } from "../src/lib/assistant-client";

const mocks = vi.hoisted(() => ({
  assistantRequest: vi.fn(),
  updateAssistantGroup: vi.fn(),
}));
vi.mock("../src/lib/assistant-client", () => mocks);

import { GroupRailMenu } from "../src/components/group-rail-menu";
import { updateAssistantGroup } from "../src/lib/assistant-client";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function group(overrides: Partial<AssistantGroup> = {}): AssistantGroup {
  return {
    groupId: "g1",
    name: "产品评审群",
    description: "每周评审",
    creatorUserId: "u1",
    primaryPresent: true,
    replyAs: null,
    lastReadAt: null,
    unreadCount: 0,
    members: [],
    createdAt: "2026-11-01T00:00:00Z",
    updatedAt: "2026-11-01T00:00:00Z",
    ...overrides,
  };
}

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.clearAllMocks();
  mocks.updateAssistantGroup.mockResolvedValue(group());
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  await act(async () => root.unmount());
  host.remove();
});

async function flush() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

async function mount(onDeleted = vi.fn()) {
  await act(async () => root.render(<GroupRailMenu group={group()} onDeleted={onDeleted} />));
  await flush();
}

async function openMenu() {
  const trigger = host.querySelector<HTMLButtonElement>('button[aria-label="产品评审群 的更多操作"]')!;
  await act(async () => trigger.click());
  await flush();
}

function buttonByText(text: string): HTMLButtonElement | undefined {
  return [...document.querySelectorAll<HTMLButtonElement>("button")].find(
    (button) => button.textContent?.trim() === text,
  );
}

function setValue(element: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const proto =
    element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value")!.set!.call(element, value);
  element.dispatchEvent(new Event("input", { bubbles: true }));
}

it("puts 群资料 above a divider and 删除群聊 below it", async () => {
  await mount();
  await openMenu();

  const items = [...document.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')];
  expect(items.map((item) => item.textContent)).toEqual(["群资料…", "删除群聊…"]);
  expect(items[1].className).toContain("danger");
  // 两个菜单项之间有一条分隔线，删除仍在最下方。
  expect(document.querySelector('[role="menu"] hr')).not.toBeNull();
  expect(document.querySelector('[role="menu"]')!.lastElementChild?.textContent).toBe("删除群聊…");
});

it("opens the profile form focused on the name, prefilled with current values", async () => {
  await mount();
  await openMenu();

  await act(async () => buttonByText("群资料…")!.click());
  await flush();

  const dialog = document.querySelector<HTMLElement>('[role="dialog"]')!;
  expect(dialog).not.toBeNull();
  const name = dialog.querySelector<HTMLInputElement>('input[aria-label="群名称"]')!;
  const description = dialog.querySelector<HTMLTextAreaElement>('textarea[aria-label="群描述"]')!;
  expect(name.value).toBe("产品评审群");
  expect(description.value).toBe("每周评审");
  expect(document.activeElement).toBe(name);
});

it("disables 保存 when the trimmed name is empty", async () => {
  await mount();
  await openMenu();
  await act(async () => buttonByText("群资料…")!.click());
  await flush();

  const name = document.querySelector<HTMLInputElement>('input[aria-label="群名称"]')!;
  await act(async () => setValue(name, "   \n  "));
  await flush();

  expect(buttonByText("保存")!.disabled).toBe(true);
  expect(mocks.updateAssistantGroup).not.toHaveBeenCalled();
});

it("saves name and description with a PATCH, then closes and broadcasts the change", async () => {
  const changed = vi.fn();
  window.addEventListener("kai:groups-changed", changed);
  await mount();
  await openMenu();
  await act(async () => buttonByText("群资料…")!.click());
  await flush();

  const name = document.querySelector<HTMLInputElement>('input[aria-label="群名称"]')!;
  const description = document.querySelector<HTMLTextAreaElement>('textarea[aria-label="群描述"]')!;
  await act(async () => {
    setValue(name, "新\n群名 ");
    setValue(description, "新的描述");
  });
  await flush();
  await act(async () => buttonByText("保存")!.click());
  await flush();

  // 群名里的换行被剥掉并 trim，描述原样去首尾空白。
  expect(mocks.updateAssistantGroup).toHaveBeenCalledWith("g1", {
    name: "新群名",
    description: "新的描述",
  });
  expect(changed).toHaveBeenCalledTimes(1);
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  window.removeEventListener("kai:groups-changed", changed);
});

it("sends description: null when the description is cleared", async () => {
  await mount();
  await openMenu();
  await act(async () => buttonByText("群资料…")!.click());
  await flush();

  const description = document.querySelector<HTMLTextAreaElement>('textarea[aria-label="群描述"]')!;
  await act(async () => setValue(description, "   "));
  await flush();
  await act(async () => buttonByText("保存")!.click());
  await flush();

  expect(mocks.updateAssistantGroup).toHaveBeenCalledWith("g1", {
    name: "产品评审群",
    description: null,
  });
});

it("keeps the form open with the typed values and shows the server error on failure", async () => {
  mocks.updateAssistantGroup.mockRejectedValueOnce(new Error("群名已被占用"));
  await mount();
  await openMenu();
  await act(async () => buttonByText("群资料…")!.click());
  await flush();

  const name = document.querySelector<HTMLInputElement>('input[aria-label="群名称"]')!;
  await act(async () => setValue(name, "重复群名"));
  await flush();
  await act(async () => buttonByText("保存")!.click());
  await flush();

  const alert = document.querySelector('[role="alert"]')!;
  expect(alert.textContent).toContain("群名已被占用");
  expect(document.querySelector('[role="dialog"]')).not.toBeNull();
  expect(document.querySelector<HTMLInputElement>('input[aria-label="群名称"]')!.value).toBe("重复群名");
});

it("closes the profile form on Escape without saving", async () => {
  await mount();
  await openMenu();
  await act(async () => buttonByText("群资料…")!.click());
  await flush();

  await act(async () => {
    document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  });
  await flush();

  expect(document.querySelector('[role="dialog"]')).toBeNull();
  expect(mocks.updateAssistantGroup).not.toHaveBeenCalled();
});

it("no longer accepts replyAs on updateAssistantGroup", () => {
  // 接话人不再可从 UI 写入：类型层面就必须拒绝 replyAs 字段。
  type Fields = Parameters<typeof updateAssistantGroup>[1];
  const fields: Fields = { name: "只改资料", description: null };
  expect(Object.keys(fields)).toEqual(["name", "description"]);
  // @ts-expect-error replyAs 不是 updateAssistantGroup 的可写字段
  const banned: Fields = { replyAs: "specialist-x" };
  expect(banned).toBeTruthy();
});
