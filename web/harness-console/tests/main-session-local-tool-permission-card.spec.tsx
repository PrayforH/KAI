// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const assistantRequest = vi.fn();

vi.mock("../src/lib/assistant-client", () => ({
  assistantRequest: (...args: unknown[]) => assistantRequest(...args),
}));

import { HandoverInlineRows } from "../src/components/handover-row";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

const specialistId = "specialist_tools";
const threadId = "thread-tools";

let root: Root | undefined;
let host: HTMLDivElement;
let fetchMock: ReturnType<typeof vi.fn>;
let config: Record<string, unknown>;

function specialist(revision = 3) {
  return {
    specialistId,
    name: "休息提醒",
    slug: "rest",
    description: "例行提醒",
    agentName: "archive-assistant-agent",
    agentVersion: "0.1.1",
    status: "active",
    revision,
    config,
    createdAt: "2026-11-01T00:00:00Z",
    updatedAt: "2026-11-01T00:00:00Z",
  };
}

beforeEach(() => {
  assistantRequest.mockReset();
  config = { required_local_tools: ["run_commands"] };
  assistantRequest.mockImplementation(
    async (path: string, method = "GET", body?: unknown) => {
      if (path === "handovers") return [];
      if (path === "specialists") return [];
      if (path === `specialists/${specialistId}` && method === "GET") {
        return { specialist: specialist(), threadId, primaryObjectiveId: "objective_1", duties: [] };
      }
      if (path === `specialists/${specialistId}/config`) {
        const next = (body as { config: Record<string, unknown> }).config;
        config = next;
        return specialist(4);
      }
      throw new Error(`unexpected request ${method} ${path}`);
    },
  );
  fetchMock = vi.fn(async (url: string) => {
    if (String(url).startsWith(`/api/agui/threads/${threadId}/history`)) {
      return Response.json({ messages: [{ id: "m1", role: "assistant", content: "做完了。" }] });
    }
    if (String(url).startsWith("/api/agui/threads?thread_id=")) {
      return Response.json([]);
    }
    throw new Error(`unexpected fetch ${url}`);
  });
  vi.stubGlobal("fetch", fetchMock);
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

async function flush(rounds = 12) {
  for (let index = 0; index < rounds; index += 1) {
    await act(async () => {
      await Promise.resolve();
    });
  }
}

async function mountWithHandover() {
  await act(async () => {
    root!.render(<HandoverInlineRows />);
  });
  await flush(2);
  await act(async () => {
    window.dispatchEvent(
      new CustomEvent("kai:handover", {
        detail: { specialist_id: specialistId, specialist_name: "休息提醒", status: "applied" },
      }),
    );
  });
  await flush();
}

function action(name: "allow" | "later"): HTMLButtonElement | null {
  return host.querySelector<HTMLButtonElement>(
    `.handover-local-tool-permission-card button[data-action="${name}"]`,
  );
}

describe("main session local tool permission card", () => {
  it("asks per ability and grants through config CAS", async () => {
    await mountWithHandover();
    const card = host.querySelector(".handover-local-tool-permission-card");
    expect(card).not.toBeNull();
    expect(card?.textContent).toContain("待你授权本地能力");
    expect(card?.textContent).toContain("允许「休息提醒」在共享电脑上运行命令吗？");

    await act(async () => action("allow")!.click());
    await flush();

    const call = assistantRequest.mock.calls.find(([path]) =>
      String(path).endsWith("/config"),
    );
    expect(call).toBeTruthy();
    const body = call![2] as {
      config: { local_tool_permissions: string[] };
      expectedRevision: number;
    };
    expect(body.config.local_tool_permissions).toEqual(["run_commands"]);
    expect(body.expectedRevision).toBe(3);
    expect(host.querySelector(".handover-local-tool-permission-card")).toBeNull();
    expect(window.location.pathname + window.location.search).toBe("/");
  });

  it("stays quiet when every declared ability is already granted", async () => {
    config = {
      required_local_tools: ["run_commands"],
      local_tool_permissions: ["run_commands"],
    };
    await mountWithHandover();
    expect(host.querySelector(".handover-local-tool-permission-card")).toBeNull();
    expect(host.textContent ?? "").not.toContain("待你授权");
  });

  it("lets the user defer without faking a grant", async () => {
    await mountWithHandover();
    await act(async () => action("later")!.click());
    await flush();
    expect(host.querySelector(".handover-local-tool-permission-card")).toBeNull();
    const configCalls = assistantRequest.mock.calls.filter(([path]) =>
      String(path).endsWith("/config"),
    );
    expect(configCalls).toHaveLength(0);
  });
});
