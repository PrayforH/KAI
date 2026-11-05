import { emitHandover } from "./helpers/handover";
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

const specialistId = "specialist_approval";
const threadId = "thread-approval";

const detail = {
  specialist: {
    specialistId,
    name: "行业快报监控",
    slug: "news",
    description: "每天汇总动态",
    agentName: "archive-assistant-agent",
    agentVersion: "0.1.1",
    status: "active",
    revision: 3,
    config: {},
    createdAt: "2026-11-01T00:00:00Z",
    updatedAt: "2026-11-01T00:00:00Z",
  },
  threadId,
  primaryObjectiveId: "objective_1",
  duties: [],
};

let root: Root | undefined;
let host: HTMLDivElement;
let fetchMock: ReturnType<typeof vi.fn>;
let approvalId: string;

beforeEach(() => {
  assistantRequest.mockReset();
  assistantRequest.mockImplementation(async (path: string) => {
    if (path === "handovers") return [];
    if (path === "specialists") return [];
    if (path === `specialists/${specialistId}`) return detail;
    throw new Error(`unexpected request ${path}`);
  });
  approvalId = "approval_1";
  fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
    if (String(url).startsWith("/api/agui/threads?thread_id=")) {
      return Response.json([
        {
          thread_id: threadId,
          status: "waiting_approval",
          run_id: "run-approval-1",
          pending_approval: {
            approval_id: approvalId,
            run_id: "run-approval-1",
            tool_name: "Bash",
            reason: "matched policy rule shell-command-review",
            argument_summary: { command: "rm -rf /tmp/scratch" },
            status: "pending",
          },
        },
      ]);
    }
    if (String(url).startsWith(`/api/agui/threads/${threadId}/history`)) {
      return Response.json({
        messages: [
          { id: "m1", role: "user", content: "跑一下清理" },
          { id: "m2", role: "assistant", content: "需要先确认这条命令。" },
        ],
      });
    }
    if (String(url).startsWith("/api/harness/approvals/") && init?.method === "PUT") {
      return new Response(null, { status: 200 });
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
    root!.render(<HandoverInlineRows messageId="assistant-run-ui-test" />);
  });
  await flush(2);
  await act(async () => {
    emitHandover({ specialist_id: specialistId, specialist_name: "行业快报监控", status: "applied" });
  });
  await flush();
}

function button(label: string): HTMLButtonElement | null {
  return (
    [...host.querySelectorAll("button")].find((node) =>
      (node.textContent ?? "").includes(label),
    ) ?? null
  );
}

describe("main session approval projection", () => {
  it("shows the pending approval and approves through the existing decide endpoint", async () => {
    await mountWithHandover();

    const card = host.querySelector(".handover-approval-card");
    expect(card).not.toBeNull();
    expect(card?.textContent).toContain("「行业快报监控」等待你审批");
    expect(card?.textContent).toContain("允许运行这条命令？");
    expect(card?.textContent).toContain("平台策略要求确认本次操作。");
    expect(card?.textContent).toContain("rm -rf /tmp/scratch");

    await act(async () => button("允许一次")!.click());
    await flush();

    const decideCall = fetchMock.mock.calls.find(
      ([url, init]) =>
        String(url) === `/api/harness/approvals/${approvalId}` &&
        (init as RequestInit)?.method === "PUT",
    );
    expect(decideCall).toBeTruthy();
    expect(JSON.parse(String((decideCall![1] as RequestInit).body))).toEqual({
      decision: "approved",
    });
    expect(host.querySelector(".handover-approval-card")).toBeNull();
    expect(window.location.pathname + window.location.search).toBe("/");
  });

  it("rejects with decision=rejected and hides the card", async () => {
    approvalId = "approval_2";
    await mountWithHandover();
    expect(host.querySelector(".handover-approval-card")).not.toBeNull();

    await act(async () => button("拒绝")!.click());
    await flush();

    const decideCall = fetchMock.mock.calls.find(
      ([url, init]) =>
        String(url) === "/api/harness/approvals/approval_2" &&
        (init as RequestInit)?.method === "PUT",
    );
    expect(decideCall).toBeTruthy();
    expect(JSON.parse(String((decideCall![1] as RequestInit).body))).toEqual({
      decision: "rejected",
    });
    expect(host.querySelector(".handover-approval-card")).toBeNull();
  });

  it("renders nothing when the thread has no pending approval", async () => {
    fetchMock.mockImplementation(async (url: string) => {
      if (String(url).startsWith("/api/agui/threads?thread_id=")) {
        return Response.json([{ thread_id: threadId, status: "succeeded", pending_approval: null }]);
      }
      if (String(url).startsWith(`/api/agui/threads/${threadId}/history`)) {
        return Response.json({ messages: [{ id: "m1", role: "assistant", content: "都办完了。" }] });
      }
      throw new Error(`unexpected fetch ${url}`);
    });
    await mountWithHandover();
    expect(host.querySelector(".handover-approval-card")).toBeNull();
    expect(host.textContent ?? "").not.toContain("等待你审批");
  });
});
