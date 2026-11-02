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

function detail(status: string) {
  return {
    specialist: {
      specialistId: "specialist_trial",
      name: "行业快报监控",
      slug: "news",
      description: "每天汇总动态",
      agentName: "archive-assistant-agent",
      agentVersion: "0.1.1",
      status,
      revision: 3,
      config: { schedule_minutes: 1440, notify_channel: "inbox" },
      createdAt: "2026-11-01T00:00:00Z",
      updatedAt: "2026-11-01T00:00:00Z",
    },
    threadId: "assistant_objective_1",
    primaryObjectiveId: "objective_1",
    duties: [{objectiveId: "objective_1", title:"trial", status:"active", revision:1, schedule:null}],
  };
}

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

async function mount(status: string) {
  assistantRequest.mockImplementation(async (path: string) => {
    if (path === "specialists/specialist_trial") return detail(status);
    if (path === "specialists/specialist_trial/trial") {
      return { executionId: "execution_trial_1", scheduled: "已排入首次检查" };
    }
    throw new Error(`unexpected request ${path}`);
  });
  await act(async () => {
    root!.render(
      <BotEditDialog specialistId="specialist_trial" onClose={() => {}} onSaved={() => {}} />,
    );
  });
  await flush();
}

function trialButton(): HTMLButtonElement | null {
  return (
    [...host.querySelectorAll("button")].find((node) =>
      (node.textContent ?? "").includes("首次试运行"),
    ) ?? null
  );
}

describe("bot edit dialog first trial run", () => {
  it("runs one trial for a ready bot and shows the real execution", async () => {
    await mount("ready");
    expect(trialButton()).not.toBeNull();
    expect(host.textContent).toContain("不会创建或修改周期规则");

    await act(async () => trialButton()!.click());
    await flush();

    const call = assistantRequest.mock.calls.find(([path]) =>
      String(path).endsWith("/trial"),
    );
    expect(call).toBeTruthy();
    expect(call![1]).toBe("POST");
    expect(call![2]).toEqual({ expectedRevision: 3 });
    expect(host.textContent).toContain("已排入首次检查（执行 execution_trial_1）");
  });

  it("hides the trial button when the bot is not ready or active", async () => {
    await mount("needs_input");
    expect(trialButton()).toBeNull();
    expect(host.textContent).not.toContain("首次试运行");
  });

  it("shows the server reason and lets the user retry after a conflict", async () => {
    await mount("active");
    assistantRequest.mockImplementation(async (path: string) => {
      if (path === "specialists/specialist_trial") return detail("active");
      if (path === "specialists/specialist_trial/trial") {
        throw new Error("配置缺项，不能试运行：schedule_minutes");
      }
      throw new Error(`unexpected request ${path}`);
    });
    await act(async () => trialButton()!.click());
    await flush();

    expect(host.textContent).toContain("配置缺项，不能试运行：schedule_minutes");
    expect(host.querySelector('[role="alert"]')?.textContent).toContain("配置缺项");
    expect(trialButton()?.disabled).toBe(false);
  });
});
