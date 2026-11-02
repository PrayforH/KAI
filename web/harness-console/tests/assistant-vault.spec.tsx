// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const assistantRequest = vi.fn();

vi.mock("../src/lib/assistant-client", () => ({
  assistantRequest: (...args: unknown[]) => assistantRequest(...args),
  saveSpecialistCredential: vi.fn(),
}));

import { AssistantVault } from "../src/components/assistant-vault";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const VAULT = [
  {
    itemId: "vault_item_1",
    category: "login",
    title: "行情网站",
    reference: "vault_abc",
    hasSecret: true,
    last4: null,
    revision: 1,
    createdAt: "2026-11-03T00:00:00Z",
    updatedAt: "2026-11-03T00:00:00Z",
  },
  {
    itemId: "vault_item_2",
    category: "card",
    title: "某张卡",
    reference: "vault_def",
    hasSecret: true,
    last4: "4242",
    revision: 1,
    createdAt: "2026-11-03T00:00:00Z",
    updatedAt: "2026-11-03T00:00:00Z",
  },
];

const SPECIALISTS = [
  {
    specialistId: "specialist_a",
    name: "资料 Bot",
    slug: "ziliau",
    description: "",
    agentName: "agent",
    agentVersion: "1.0",
    status: "ready",
    revision: 1,
    config: {},
    createdAt: "2026-11-03T00:00:00Z",
    updatedAt: "2026-11-03T00:00:00Z",
  },
];

let root: Root | undefined;
let host: HTMLDivElement;

beforeEach(() => {
  assistantRequest.mockReset();
  assistantRequest.mockImplementation(async (path: string, method = "GET") => {
    if (path === "vault" && method === "GET") return VAULT;
    if (path === "specialists" && method === "GET") return SPECIALISTS;
    if (path.startsWith("vault/") && method === "DELETE") return { deleted: true };
    return {};
  });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined;
  host.remove();
});

async function flush(rounds = 6) {
  for (let index = 0; index < rounds; index += 1) {
    await act(async () => {
      await Promise.resolve();
    });
  }
}

async function mount() {
  await act(async () => {
    root!.render(<AssistantVault />);
  });
  await flush();
}

function clickButton(label: string) {
  const button = [...host.querySelectorAll("button")].find(
    node => node.textContent?.trim() === label,
  );
  return button as HTMLButtonElement | undefined;
}

describe("assistant vault", () => {
  it("lists masked metadata only and never the value", async () => {
    await mount();
    expect(host.textContent).toContain("行情网站");
    expect(host.textContent).toContain("登录密钥");
    expect(host.textContent).toContain("末四位 4242");
    // 值不在列表里，只有「已保存」这类状态。
    expect(host.textContent).not.toContain("hunter2");
  });

  it("filters by category and searches by title", async () => {
    await mount();
    await act(async () => clickButton("卡片")!.click());
    await flush();
    expect(host.textContent).toContain("某张卡");
    expect(host.textContent).not.toContain("行情网站");

    await act(async () => clickButton("全部")!.click());
    await flush();
    const search = host.querySelector<HTMLInputElement>('input[aria-label="搜索资料"]')!;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, "value",
      )!.set!;
      setter.call(search, "行情");
      search.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await flush();
    expect(host.textContent).toContain("行情网站");
    expect(host.textContent).not.toContain("某张卡");
  });

  it("deletes only after an explicit confirmation", async () => {
    await mount();
    await act(async () => clickButton("删除")!.click());
    await flush();
    // 确认框先拦住：还没有发出删除请求。
    expect(assistantRequest.mock.calls.some(([path, method]) =>
      String(path).startsWith("vault/") && method === "DELETE")).toBe(false);
    // 确认框挂在 body 上（portal），不在组件容器里。
    expect(document.body.textContent).toContain("删除这条资料");

    const confirm = [...document.querySelectorAll("button")].find(
      node => node.textContent?.trim() === "删除资料",
    ) as HTMLButtonElement | undefined;
    await act(async () => confirm!.click());
    await flush();
    const call = assistantRequest.mock.calls.find(([, method]) => method === "DELETE");
    expect(call).toBeTruthy();
    expect(call![2]).toEqual({ confirmTitle: "行情网站" });
  });

  it("offers per-bot authorisation instead of sharing everything", async () => {
    await mount();
    expect(host.textContent).toContain("授权给 资料 Bot");
    await act(async () => clickButton("授权给 资料 Bot")!.click());
    await flush();
    const call = assistantRequest.mock.calls.find(([path]) => String(path).includes("/grant"));
    expect(call).toBeTruthy();
    expect(call![2]).toEqual({
      specialistId: "specialist_a",
      granted: true,
      expectedRevision: 1,
    });
  });
});
