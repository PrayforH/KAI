// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const assistantRequest = vi.fn();

vi.mock("../src/lib/assistant-client", () => ({
  assistantRequest: (...args: unknown[]) => assistantRequest(...args),
  saveSpecialistCredential: vi.fn(),
}));

import { BotEditDialog } from "../src/components/bot-edit-dialog";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | undefined;
let host: HTMLDivElement;
let events: string[];

function specialist(status: string, revision = 3) {
  return {
    specialist: {
      specialistId: "specialist_status",
      name: "行业快报监控",
      slug: "news",
      description: "每天汇总动态",
      agentName: "archive-assistant-agent",
      agentVersion: "0.1.1",
      status,
      revision,
      config: { schedule_minutes: 1440, notify_channel: "inbox" },
      createdAt: "2026-11-01T00:00:00Z",
      updatedAt: "2026-11-01T00:00:00Z",
    },
    threadId: "assistant_objective_1",
    primaryObjectiveId: "objective_1",
    duties: [],
  };
}

beforeEach(() => {
  assistantRequest.mockReset();
  events = [];
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  root = undefined;
  host.remove();
});

async function flush(rounds = 8) {
  for (let index = 0; index < rounds; index += 1) {
    await act(async () => {
      await Promise.resolve();
    });
  }
}

async function mount(status: string, nextStatus = status) {
  assistantRequest.mockImplementation(async (path: string, method = "GET", body?: unknown) => {
    if (path === "specialists/specialist_status" && method === "GET") {
      return specialist(status);
    }
    if (path === "specialists/specialist_status/status") {
      const decision = (body as { status: string }).status;
      return { ...specialist(status).specialist, status: decision, revision: 4 };
    }
    throw new Error(`unexpected request ${method} ${path}`);
  });
  const onEvent = (event: Event) => {
    events.push(String((event as CustomEvent).type));
  };
  window.addEventListener("kai:specialists-changed", onEvent);
  await act(async () => {
    root!.render(
      <BotEditDialog specialistId="specialist_status" onClose={() => {}} onSaved={() => {}} />,
    );
  });
  await flush();
  return () => window.removeEventListener("kai:specialists-changed", onEvent);
}

function action(name: "enable" | "pause" | "resume"): HTMLButtonElement | null {
  return host.querySelector<HTMLButtonElement>(`button[data-action="${name}"]`);
}

describe("bot edit dialog enable / pause", () => {
  it("enables a ready bot with the current revision and flips to pause", async () => {
    const cleanup = await mount("ready");
    expect(action("enable")?.textContent).toContain("启用持续运行");
    expect(action("pause")).toBeNull();
    expect(host.textContent).toContain("启用后按检查频率持续运行");

    await act(async () => action("enable")!.click());
    await flush();

    const call = assistantRequest.mock.calls.find(([path]) =>
      String(path).endsWith("/status"),
    );
    expect(call).toBeTruthy();
    expect(call![1]).toBe("POST");
    expect(call![2]).toEqual({ status: "active", expectedRevision: 3 });
    expect(host.textContent).toContain("已启用持续运行");
    // 服务端返回的 revision/状态生效：主按钮切成「暂停」。
    expect(action("pause")).not.toBeNull();
    expect(action("enable")).toBeNull();
    expect(events).toContain("kai:specialists-changed");
    cleanup();
  });

  it("pauses an active bot", async () => {
    const cleanup = await mount("active");
    expect(action("pause")?.textContent).toContain("暂停");
    expect(action("enable")).toBeNull();

    await act(async () => action("pause")!.click());
    await flush();

    const call = assistantRequest.mock.calls.find(([path]) =>
      String(path).endsWith("/status"),
    );
    expect(call![2]).toEqual({ status: "paused", expectedRevision: 3 });
    expect(host.textContent).toContain("已暂停");
    cleanup();
  });

  it("shows no status action for needs_input bots", async () => {
    const cleanupNeeds = await mount("needs_input");
    expect(action("enable")).toBeNull();
    expect(action("pause")).toBeNull();
    cleanupNeeds();
  });

  it("offers only resume for a paused bot", async () => {
    const cleanup = await mount("paused");
    expect(action("resume")?.textContent).toContain("恢复持续运行");
    expect(action("enable")).toBeNull();
    expect(action("pause")).toBeNull();
    expect(host.textContent).toContain("已暂停；点「恢复持续运行」会重新按频率检查");
    cleanup();
  });

  it("resumes a paused bot back to active", async () => {
    const cleanup = await mount("paused");
    await act(async () => action("resume")!.click());
    await flush();

    const call = assistantRequest.mock.calls.find(([path]) =>
      String(path).endsWith("/status"),
    );
    expect(call![2]).toEqual({ status: "active", expectedRevision: 3 });
    expect(host.textContent).toContain("已恢复持续运行");
    expect(action("pause")).not.toBeNull();
    expect(action("resume")).toBeNull();
    expect(events).toContain("kai:specialists-changed");
    cleanup();
  });

  it("keeps the resume button retryable when the server rejects", async () => {
    const cleanup = await mount("paused");
    assistantRequest.mockImplementation(async (path: string) => {
      if (path === "specialists/specialist_status") return specialist("paused");
      if (path === "specialists/specialist_status/status") {
        throw new Error("只有 ready 或 paused 状态可以启用持续运行");
      }
      throw new Error(`unexpected request ${path}`);
    });
    await act(async () => action("resume")!.click());
    await flush();
    expect(host.querySelector('[role="alert"]')?.textContent).toContain("ready 或 paused");
    expect(action("resume")?.disabled).toBe(false);
    cleanup();
  });

  it("shows the server conflict and keeps the button retryable", async () => {
    const cleanup = await mount("ready");
    assistantRequest.mockImplementation(async (path: string) => {
      if (path === "specialists/specialist_status") return specialist("ready");
      if (path === "specialists/specialist_status/status") {
        throw new Error("配置缺项，不能启用：notify_channel");
      }
      throw new Error(`unexpected request ${path}`);
    });
    await act(async () => action("enable")!.click());
    await flush();

    expect(host.querySelector('[role="alert"]')?.textContent).toContain(
      "配置缺项，不能启用：notify_channel",
    );
    expect(action("enable")?.disabled).toBe(false);
    cleanup();
  });
});
