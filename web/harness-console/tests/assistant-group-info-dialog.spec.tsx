// @vitest-environment jsdom
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AssistantGroupInfoDialog } from "../src/components/assistant-group-info-dialog";
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
      { memberId: "s1", kind: "specialist", name: "架构评审官", specialistId: "specialist_arch", status: "active", joinedAt: "2026-11-01T00:00:00Z" },
      { memberId: "s2", kind: "specialist", name: "已退出的助理", specialistId: "specialist_gone", status: "removed", joinedAt: "2026-11-01T00:00:00Z" },
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
  host.replaceChildren();
});

function render(element: ReactElement) {
  return act(async () => root!.render(element));
}

const dialog = () => host.querySelector<HTMLElement>('[role="dialog"][aria-modal="true"]')!;

describe("群资料弹窗", () => {
  it("shows the replier, the description and an active member count", async () => {
    await render(<AssistantGroupInfoDialog group={group({ description: "  KAI 项目研发协作组。  " })}
      onMembers={vi.fn()} onClose={vi.fn()} />);

    expect(dialog().textContent).toContain("产品评审群");
    expect(dialog().textContent).toContain("谁回复");
    expect(dialog().textContent).toContain("主助理");
    expect(dialog().textContent).toContain("未 @ 时由它接话");
    expect(dialog().textContent).toContain("KAI 项目研发协作组。");
    // 只数在群成员：3 个 active，1 个已退出。
    expect(dialog().textContent).toContain("3 人");
  });

  it("falls back to 主助理 and an empty-description hint", async () => {
    await render(<AssistantGroupInfoDialog group={group()} onMembers={vi.fn()} onClose={vi.fn()} />);

    expect(dialog().textContent).toContain("还没有说明");
    expect(dialog().textContent).not.toContain("undefined");
  });

  it("shows the legacy replier by name when the group still carries one", async () => {
    await render(<AssistantGroupInfoDialog group={group({ replyAs: "specialist_arch" })}
      onMembers={vi.fn()} onClose={vi.fn()} />);

    expect(dialog().textContent).toContain("架构评审官");
  });

  it("jumps to member management and closes on Escape", async () => {
    const onMembers = vi.fn();
    const onClose = vi.fn();
    await render(<AssistantGroupInfoDialog group={group()} onMembers={onMembers} onClose={onClose} />);

    const row = [...dialog().querySelectorAll("button")].find((button) => button.textContent?.includes("人"))!;
    await act(async () => row.click());
    expect(onMembers).toHaveBeenCalledTimes(1);

    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe("群资料的职责分工", () => {
  const specialist = (id: string, description: string) => ({
    specialistId: id,
    name: id,
    slug: id,
    description,
    agentName: "kai",
    agentVersion: "1",
    status: "active" as const,
    revision: 1,
    config: {},
    createdAt: "2026-11-01T00:00:00Z",
    updatedAt: "2026-11-01T00:00:00Z",
  });

  it("lists every active member with the specialist's real description", async () => {
    await render(<AssistantGroupInfoDialog group={group()}
      specialists={[specialist("specialist_arch", "负责架构与接口评审")]}
      onMembers={vi.fn()} onClose={vi.fn()} />);

    expect(dialog().textContent).toContain("职责分工");
    expect(dialog().textContent).toContain("架构评审官");
    expect(dialog().textContent).toContain("负责架构与接口评审");
    // 创建者与主助理按角色说明；已退出的成员不出现。
    expect(dialog().textContent).toContain("创建者");
    expect(dialog().textContent).toContain("未 @ 时由它接话");
    expect(dialog().textContent).not.toContain("已退出的助理");
  });

  it("says 未填写职责 instead of inventing text when a specialist has none", async () => {
    await render(<AssistantGroupInfoDialog group={group()}
      specialists={[specialist("specialist_arch", "   ")]}
      onMembers={vi.fn()} onClose={vi.fn()} />);

    const row = [...dialog().querySelectorAll("li")]
      .find(item => item.textContent?.includes("架构评审官"))!;
    expect(row.textContent).toContain("未填写职责");
    expect(row.querySelector('[title="未填写职责"]')).not.toBeNull();
  });

  it("still works without the specialists list", async () => {
    await render(<AssistantGroupInfoDialog group={group()} onMembers={vi.fn()} onClose={vi.fn()} />);
    expect(dialog().textContent).toContain("未填写职责");
    expect(dialog().textContent).not.toContain("undefined");
  });
});
