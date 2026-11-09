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
import { BotSidePanel } from "../src/components/bot-side-panel";

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
  vi.unstubAllGlobals();
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
  it("keeps the name and restores the Bot settings entries without a more menu", () => {
    expect(pageSource).toContain("task-context-bot-name");
    expect(pageSource).not.toContain("AssistantHeaderAvatar");
    expect(pageSource).not.toContain("task-context-bot-hint");
    expect(pageSource).not.toContain("task-context-bot-more");
  });

  it("opens the profile dialog from the name", () => {
    expect(pageSource).toContain("BotProfileDialog");
    expect(pageSource).toContain('setBotSettings({ id: bot.specialistId, tab: "details" })');
    expect(pageSource).toContain('setBotSettings({ id: botParam, tab: "duties" })');
  });

  it("switches from details to the same Bot's routines", async () => {
    await mount();
    const tabs = host.querySelectorAll<HTMLButtonElement>('[role="tab"]');
    expect([...tabs].map(tab => tab.textContent)).toEqual(["详情", "例行任务"]);
    expect(tabs[0].getAttribute("aria-selected")).toBe("true");
    await act(async () => tabs[1].click());
    await flush();
    expect(tabs[1].getAttribute("aria-selected")).toBe("true");
    expect(host.querySelector('[role="tabpanel"]')?.textContent).toContain("这个 Bot 还没有职责。");
    expect(host.querySelector('input[aria-label="姓名"]')).toBeNull();
  });

  it("keeps the IM channel status on the main assistant header", () => {
    // 通道状态属于主助理的跨渠道会话，不能挂在「资料记录建出来没有」这个条件上：
    // 主助理资料一旦存在，用 !bot 判断就会把飞书/微信状态整行丢掉。
    expect(pageSource).toContain("{!botParam && readyConversation === threadId && <AssistantChannelStatus />}");
    expect(pageSource).not.toContain("{!bot && <AssistantChannelStatus />}");
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

  it("saves the three profile fields plus appearance", async () => {
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
    // 资料弹窗现在也带外观（页头与侧栏共用同一个弹窗）；除这三项资料外不再提交别的配置。
    expect(call![2]).toEqual({
      name: "演示 Bot",
      title: "营销",
      description: "盯信号",
      appearance: { color: "#126fd4", avatar: "blob" },
      expectedRevision: 3,
    });
  });
});

describe("Bot side panel", () => {
  it("shows scoped activity, computer and library without an empty suggestions section", async () => {
    const openComputer = vi.fn();
    const manageDuties = vi.fn();
    assistantRequest.mockImplementation(async (path: string) => {
      if (path === "specialists/specialist_demo") return detail();
      if (path === "vault") return [
        { itemId: "granted", title: "这只 Bot 的资料", grants: [{ specialistId: "specialist_demo", status: "ok" }] },
        { itemId: "other", title: "另一只 Bot 的资料", grants: [{ specialistId: "specialist_other", status: "ok" }] },
      ];
      throw new Error(path);
    });
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      expect(url).toContain("thread_id=bot-thread");
      return Response.json([
        { artifact_id: "a", run_id: "r", thread_id: "bot-thread", name: "交付.txt" },
        { artifact_id: "b", run_id: "r", thread_id: "other-thread", name: "其他.txt" },
      ]);
    }));
    await act(async () => root!.render(<BotSidePanel botId="specialist_demo" name="演示 Bot" tone="#126fd4"
      threadId="bot-thread" ready={false} onClose={vi.fn()} onDetails={vi.fn()} onDuties={manageDuties}
      onComputer={openComputer} />));
    await flush();
    expect(host.textContent).toContain("即将进行");
    expect(host.textContent).toContain("Bot 的电脑");
    expect(host.textContent).not.toContain("建议");
    await act(async () => buttonByText("管理例行任务")!.click());
    expect(manageDuties).toHaveBeenCalledOnce();
    await act(async () => buttonByText("资料库")!.click());
    await flush();
    expect(host.textContent).toContain("交付.txt");
    expect(host.textContent).toContain("这只 Bot 的资料");
    expect(host.textContent).not.toContain("其他.txt");
    expect(host.textContent).not.toContain("另一只 Bot 的资料");
    expect(host.textContent).not.toContain("Bot 的电脑");
  });
});
