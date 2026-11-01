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

function detail(config: Record<string, unknown> = {}, revision = 3) {
  return {
    specialist: {
      specialistId: "specialist_tools",
      name: "行业快报监控",
      slug: "news",
      description: "每天汇总动态",
      agentName: "archive-assistant-agent",
      agentVersion: "0.1.1",
      status: "active",
      revision,
      config,
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

async function mount(config: Record<string, unknown> = {}) {
  assistantRequest.mockImplementation(async (path: string) => {
    if (path === "specialists/specialist_tools") return detail(config);
    throw new Error(`unexpected request ${path}`);
  });
  await act(async () => {
    root!.render(
      <BotEditDialog specialistId="specialist_tools" onClose={() => {}} onSaved={() => {}} />,
    );
  });
  await flush();
}

function checkbox(key: string): HTMLInputElement | null {
  return host.querySelector<HTMLInputElement>(`input[data-tool="${key}"]`);
}

describe("bot edit dialog local tool permissions", () => {
  it("shows the four abilities unchecked and grants one via config CAS", async () => {
    await mount();
    expect(host.textContent).toContain("本地工具权限");
    const labels = [...host.querySelectorAll<HTMLInputElement>("input[data-tool]")].map(
      (input) => input.getAttribute("aria-label"),
    );
    expect(labels).toEqual([
      "在共享电脑上运行命令",
      "访问私人文件",
      "使用已连接的应用",
      "运行后台/例行任务",
    ]);
    expect(checkbox("run_commands")?.checked).toBe(false);

    assistantRequest.mockImplementation(async (path: string, method = "GET", body?: unknown) => {
      if (path === "specialists/specialist_tools" && method === "GET") return detail();
      if (path === "specialists/specialist_tools/config") {
        return detail({ local_tool_permissions: ["run_commands"] }, 4).specialist;
      }
      throw new Error(`unexpected request ${method} ${path} ${JSON.stringify(body)}`);
    });
    await act(async () => checkbox("run_commands")!.click());
    await flush();

    const call = assistantRequest.mock.calls.find(([path]) =>
      String(path).endsWith("/config"),
    );
    expect(call).toBeTruthy();
    expect(call![1]).toBe("POST");
    const body = call![2] as {
      config: { local_tool_permissions: string[] };
      expectedRevision: number;
    };
    expect(body.config.local_tool_permissions).toEqual(["run_commands"]);
    expect(body.expectedRevision).toBe(3);
    expect(checkbox("run_commands")?.checked).toBe(true);
  });

  it("keeps the toggle unchecked and shows the server error on failure", async () => {
    await mount();
    assistantRequest.mockImplementation(async (path: string) => {
      if (path === "specialists/specialist_tools") return detail();
      if (path === "specialists/specialist_tools/config") {
        throw new Error("配置缺项，不能启用：notify_channel");
      }
      throw new Error(`unexpected request ${path}`);
    });
    await act(async () => checkbox("run_commands")!.click());
    await flush();

    expect(host.querySelector('[role="alert"]')?.textContent).toContain("配置缺项");
    expect(checkbox("run_commands")?.checked).toBe(false);
  });

  it("reflects already granted abilities", async () => {
    await mount({ local_tool_permissions: ["access_private_files"] });
    expect(checkbox("access_private_files")?.checked).toBe(true);
    expect(checkbox("run_commands")?.checked).toBe(false);
  });
});
