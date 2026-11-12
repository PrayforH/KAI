// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const createGroup = vi.fn();
const addMember = vi.fn();

vi.mock("../src/lib/assistant-client", () => ({
  createAssistantGroup: (...args: unknown[]) => createGroup(...args),
  addAssistantGroupMember: (...args: unknown[]) => addMember(...args),
}));

import { CreateGroupDialog } from "../src/components/create-group-dialog";
import type { Specialist } from "../src/lib/assistant-client";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

function specialist(id: string, name: string): Specialist {
  return {
    specialistId: id,
    slug: id,
    name,
    status: "active",
    config: {},
    createdAt: "2026-11-01T00:00:00Z",
    updatedAt: "2026-11-01T00:00:00Z",
  } as Specialist;
}

const bots = [specialist("coding", "编码协同"), specialist("weather", "天气提醒")];

let root: Root | undefined;
let host: HTMLDivElement;

beforeEach(() => {
  createGroup.mockReset();
  addMember.mockReset();
  createGroup.mockResolvedValue({ groupId: "grp_new", name: "演示群" });
  addMember.mockResolvedValue(undefined);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined;
  host.remove();
});

const text = () => host.textContent ?? "";
const byText = (label: string) =>
  [...host.querySelectorAll("button")].find((node) => node.textContent === label)!;
const typeName = async (value: string) => {
  const input = host.querySelector("input:not([type='checkbox'])") as HTMLInputElement;
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  await act(async () => {
    setter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
};

describe("create group dialog uses the rail's specialist list", () => {
  it("shows specialists immediately when the rail list is ready and creates with the checked ones", async () => {
    const onCreated = vi.fn();
    await act(async () => {
      root!.render(
        <CreateGroupDialog
          specialists={bots}
          ready
          error={null}
          onRetry={vi.fn()}
          onClose={vi.fn()}
          onCreated={onCreated}
        />,
      );
    });
    expect(text()).not.toContain("正在加载子助理");
    expect(text()).not.toContain("还没有可拉入的子助理");
    expect(host.querySelectorAll("input[type='checkbox']")).toHaveLength(2);

    await act(async () => {
      (host.querySelector("input[type='checkbox']") as HTMLInputElement).click();
    });
    await typeName("演示群");
    await act(async () => {
      byText("创建群聊").click();
    });
    await act(async () => {});

    expect(createGroup).toHaveBeenCalledWith("演示群");
    expect(addMember).toHaveBeenCalledTimes(1);
    expect(addMember).toHaveBeenCalledWith("grp_new", "coding");
    expect(onCreated).toHaveBeenCalledWith("grp_new");
  });

  it("still creates while the rail list is loading", async () => {
    const onCreated = vi.fn();
    await act(async () => {
      root!.render(
        <CreateGroupDialog
          specialists={[]}
          ready={false}
          error={null}
          onRetry={vi.fn()}
          onClose={vi.fn()}
          onCreated={onCreated}
        />,
      );
    });
    expect(text()).toContain("正在加载子助理");
    expect(text()).toContain("也可以先建群");
    expect(text()).not.toContain("还没有可拉入的子助理");
    await typeName("演示群");
    // 名单没加载完也不能让「创建群聊」点不动。
    expect(byText("创建群聊").disabled).toBe(false);
    await act(async () => byText("创建群聊").click());
    await act(async () => {});
    expect(createGroup).toHaveBeenCalledWith("演示群");
    expect(onCreated).toHaveBeenCalledWith("grp_new");
  });

  it("enters the group even when a member invitation fails", async () => {
    addMember.mockRejectedValue(new Error("成员邀请失败"));
    const onCreated = vi.fn();
    await act(async () => {
      root!.render(
        <CreateGroupDialog
          specialists={bots}
          ready
          error={null}
          onRetry={vi.fn()}
          onClose={vi.fn()}
          onCreated={onCreated}
        />,
      );
    });
    await act(async () => {
      (host.querySelector("input[type='checkbox']") as HTMLInputElement).click();
    });
    await typeName("演示群");
    await act(async () => byText("创建群聊").click());
    await act(async () => {});
    // 群已建成：不能让整次创建显示失败，否则重试会再建一个重复群。
    expect(onCreated).toHaveBeenCalledWith("grp_new");
    expect(text()).not.toContain("成员邀请失败");
  });

  it("shows a clear failure with retry instead of an empty state", async () => {
    const onRetry = vi.fn();
    await act(async () => {
      root!.render(
        <CreateGroupDialog
          specialists={[]}
          ready={false}
          error="网络错误"
          onRetry={onRetry}
          onClose={vi.fn()}
          onCreated={vi.fn()}
        />,
      );
    });
    expect(text()).toContain("子助理加载失败");
    expect(text()).not.toContain("还没有可拉入的子助理");
    await typeName("演示群");
    // 名单失败也不挡住建群，仍可只建创建者+主助理的群。
    expect(byText("创建群聊").disabled).toBe(false);
    await act(async () => byText("重试").click());
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("allows creator+primary only after a successful empty load", async () => {
    await act(async () => {
      root!.render(
        <CreateGroupDialog
          specialists={[]}
          ready
          error={null}
          onRetry={vi.fn()}
          onClose={vi.fn()}
          onCreated={vi.fn()}
        />,
      );
    });
    expect(text()).toContain("还没有可拉入的子助理");
    await typeName("空群");
    expect(byText("创建群聊").disabled).toBe(false);
  });
});
