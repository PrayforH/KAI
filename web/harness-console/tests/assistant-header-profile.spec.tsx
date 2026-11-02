// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const assistantRequest = vi.fn();

vi.mock("../src/lib/assistant-client", () => ({
  assistantRequest: (...args: unknown[]) => assistantRequest(...args),
  saveSpecialistCredential: vi.fn(),
}));

import { BotProfileDialog } from "../src/components/bot-profile-dialog";

const pageSource = readFileSync(join(process.cwd(), "src/app/page.tsx"), "utf8");

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | undefined;
let host: HTMLDivElement;

beforeEach(() => {
  assistantRequest.mockReset();
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

function detail() {
  return {
    specialist: {
      specialistId: "specialist_demo",
      name: "演示 Bot",
      slug: "demo",
      description: "盯信号",
      agentName: "agent",
      agentVersion: "1.0",
      status: "ready",
      revision: 3,
      config: { title: "研究" },
      createdAt: "2026-11-03T00:00:00Z",
      updatedAt: "2026-11-03T00:00:00Z",
    },
    duties: [],
  };
}

function buttonByText(text: string): HTMLButtonElement | null {
  return [...host.querySelectorAll("button")].find(node => node.textContent?.trim() === text) ?? null;
}

async function mount() {
  assistantRequest.mockImplementation(async (path: string, method = "GET") => {
    if (path === "specialists/specialist_demo" && method === "GET") return detail();
    if (path === "specialists/specialist_demo/profile") return detail().specialist;
    throw new Error(`unexpected request ${method} ${path}`);
  });
  await act(async () => {
    root!.render(
      <BotProfileDialog specialistId="specialist_demo" onClose={() => {}} onSaved={() => {}} />,
    );
  });
  await flush();
}

describe("assistant session header", () => {
  it("shows the name only: no avatar, no role subtitle, no more button", () => {
    expect(pageSource).toContain("task-context-bot-name");
    expect(pageSource).not.toContain("AssistantHeaderAvatar");
    expect(pageSource).not.toContain("task-context-bot-hint");
    expect(pageSource).not.toContain("task-context-bot-more");
  });

  it("opens the profile dialog from the name", () => {
    expect(pageSource).toContain("BotProfileDialog");
    expect(pageSource).toContain("onClick={() => setBotProfileOpen(true)}");
  });
});

describe("bot profile dialog", () => {
  it("offers exactly name, optional title and description", async () => {
    await mount();
    const labels = [...host.querySelectorAll("label > span")].map(node => node.textContent);
    expect(labels).toEqual(["姓名", "标题（可选）", "描述"]);
    // 资料表单里不出现权限、计划或工具配置。
    expect(host.textContent).not.toContain("交办与执行方式");
    expect(host.textContent).not.toContain("本地能力");
    expect(host.textContent).not.toContain("凭据");
  });

  it("saves only the three profile fields", async () => {
    await mount();
    const title = host.querySelector<HTMLInputElement>('input[aria-label="标题"]')!;
    await act(async () => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype, "value",
      )!.set!;
      setter.call(title, "营销");
      title.dispatchEvent(new Event("input", { bubbles: true }));
    });
    await flush();
    await act(async () => {
      buttonByText("保存")!.click();
    });
    await flush();
    const call = assistantRequest.mock.calls.find(([path]) =>
      String(path).endsWith("/profile"),
    );
    expect(call).toBeTruthy();
    expect(call![1]).toBe("POST");
    expect(call![2]).toEqual({
      name: "演示 Bot",
      title: "营销",
      description: "盯信号",
      expectedRevision: 3,
    });
  });
});
