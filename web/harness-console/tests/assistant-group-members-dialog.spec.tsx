// @vitest-environment jsdom
import { act, type ReactElement } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AssistantGroupMembersDialog, type MemberChange } from "../src/components/assistant-group-members-dialog";
import type { AssistantGroup, Specialist } from "../src/lib/assistant-client";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function group(): AssistantGroup {
  return {
    groupId: "g1",
    name: "KAI项目研发",
    creatorUserId: "u1",
    primaryPresent: true,
    replyAs: null,
    lastReadAt: null,
    unreadCount: 0,
    members: [
      { memberId: "u1", kind: "creator", name: "我", specialistId: null, status: "active", joinedAt: "2026-11-01T00:00:00Z" },
      { memberId: "primary", kind: "primary", name: "主助理", specialistId: null, status: "active", joinedAt: "2026-11-01T00:00:00Z" },
      { memberId: "m-a", kind: "specialist", name: "前沿动态", specialistId: "spec-a", status: "active", joinedAt: "2026-11-01T00:00:00Z" },
    ],
    createdAt: "2026-11-01T00:00:00Z",
    updatedAt: "2026-11-01T00:00:00Z",
  };
}

function specialist(specialistId: string, name: string, status: "active" | "archived" = "active"): Specialist {
  return {
    specialistId,
    name,
    status,
    config: {},
  } as unknown as Specialist;
}

const specialists = [
  specialist("spec-a", "前沿动态"),
  specialist("spec-b", "质量评测"),
  specialist("spec-c", "已归档", "archived"),
];

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

const boxes = () => Array.from(host.querySelectorAll<HTMLInputElement>("input[type=checkbox]"));

function nameOf(input: HTMLInputElement) {
  return input.closest("label")?.querySelector("span:nth-of-type(2)")?.textContent ?? "";
}

describe("AssistantGroupMembersDialog", () => {
  it("renders creator and primary checked and locked, and hides archived specialists", async () => {
    await render(<AssistantGroupMembersDialog group={group()} specialists={specialists}
      onApply={async () => null} onClose={() => {}} />);

    const rows = boxes();
    expect(rows.map(nameOf)).toEqual(["我", "主助理", "前沿动态", "质量评测"]);
    expect(rows[0].checked).toBe(true);
    expect(rows[0].disabled).toBe(true);
    expect(rows[1].checked).toBe(true);
    expect(rows[1].disabled).toBe(true);
    expect(rows[2].checked).toBe(true);
    expect(rows[2].disabled).toBe(false);
    expect(rows[3].checked).toBe(false);
  });

  it("toggles only local state and sends nothing until 保存", async () => {
    const onApply = vi.fn(async (_change: MemberChange) => null);
    await render(<AssistantGroupMembersDialog group={group()} specialists={specialists}
      onApply={onApply} onClose={() => {}} />);

    await act(async () => boxes()[2].click());
    await act(async () => boxes()[3].click());

    expect(onApply).not.toHaveBeenCalled();
    expect(boxes()[2].checked).toBe(false);
    expect(boxes()[3].checked).toBe(true);
    expect(host.textContent).toContain("保存 · 3 个成员");
  });

  it("saves exactly the diff in one go", async () => {
    const onApply = vi.fn(async (_change: MemberChange): Promise<string | null> => null);
    const onClose = vi.fn();
    await render(<AssistantGroupMembersDialog group={group()} specialists={specialists}
      onApply={onApply} onClose={onClose} />);

    await act(async () => boxes()[2].click());
    await act(async () => boxes()[3].click());
    const save = Array.from(host.querySelectorAll("button")).find(button => button.textContent?.startsWith("保存"));
    await act(async () => save!.click());

    expect(onApply).toHaveBeenCalledTimes(1);
    expect(onApply.mock.calls[0][0]).toEqual({
      add: ["spec-b"],
      remove: [{ memberId: "m-a", name: "前沿动态" }],
    });
    expect(onClose).toHaveBeenCalled();
  });

  it("keeps the dialog open with a readable error when saving fails", async () => {
    const onClose = vi.fn();
    await render(<AssistantGroupMembersDialog group={group()} specialists={specialists}
      onApply={async () => "网络异常，成员未保存"} onClose={onClose} />);

    await act(async () => boxes()[3].click());
    const save = Array.from(host.querySelectorAll("button")).find(button => button.textContent?.startsWith("保存"));
    await act(async () => save!.click());

    expect(onClose).not.toHaveBeenCalled();
    expect(host.querySelector("[role=alert]")?.textContent).toBe("网络异常，成员未保存");
    expect(boxes()[3].checked).toBe(true);
  });

  it("is a modal dialog that Esc closes", async () => {
    const onClose = vi.fn();
    await render(<AssistantGroupMembersDialog group={group()} specialists={specialists}
      onApply={async () => null} onClose={onClose} />);

    const dialog = host.querySelector("[role=dialog]");
    expect(dialog?.getAttribute("aria-modal")).toBe("true");
    await act(async () => {
      document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(onClose).toHaveBeenCalled();
  });
});
