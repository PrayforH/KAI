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

const specialistId = "specialist_ask";
const threadId = "thread-bot-1";
const askMessage = [
  "先确认一下参数。",
  "",
  "```harness-input",
  JSON.stringify({
    version: 1,
    title: "请确认参数",
    questions: [{ id: "target", label: "目标距离", type: "text", options: [] }],
  }),
  "```",
].join("\n");

const detail = {
  specialist: {
    specialistId,
    name: "休息提醒",
    slug: "rest",
    description: "例行提醒",
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
let historyMessages: unknown[];
const fetchMock = vi.fn();

beforeEach(() => {
  assistantRequest.mockReset();
  fetchMock.mockReset();
  historyMessages = [
    { id: "m1", role: "user", content: "把目标改成 8 公里" },
    { id: "m2", role: "assistant", content: askMessage },
    { id: "m3", role: "tool", content: '{"status":"ready"}' },
  ];
  assistantRequest.mockImplementation(async (path: string) => {
    if (path === "handovers") return [];
    if (path === "specialists") return [];
    if (path === `specialists/${specialistId}`) return detail;
    throw new Error(`unexpected request ${path}`);
  });
  fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
    if (url.startsWith(`/api/agui/threads/${threadId}/history`)) {
      return Response.json({ messages: historyMessages, status: "succeeded" });
    }
    if (url.startsWith("/api/agui?") && init?.method === "POST") {
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

async function flush(rounds = 10) {
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
    emitHandover({ specialist_id: specialistId, specialist_name: "休息提醒", status: "applied" });
  });
  await flush();
}

function setInput(input: HTMLInputElement, value: string) {
  Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!.call(input, value);
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

describe("main session ask projection", () => {
  it("shows the bot's pending ask and writes the answer into that bot thread", async () => {
    await mountWithHandover();

    const askCard = host.querySelector(".handover-ask-card");
    expect(askCard).not.toBeNull();
    expect(askCard?.textContent).toContain("「休息提醒」正在等你回答");
    expect(askCard?.textContent).toContain("请确认参数");
    expect(askCard?.textContent).toContain("目标距离");

    const input = host.querySelector<HTMLInputElement>('input[aria-label="目标距离 · 填写回答"]')!;
    await act(async () => setInput(input, "8 公里"));
    const submit = [...host.querySelectorAll("button")].find((node) =>
      (node.textContent ?? "").includes("提交回答"),
    )!;
    await act(async () => submit.click());
    await flush();

    const aguiCall = fetchMock.mock.calls.find(
      ([url, init]) => String(url).startsWith("/api/agui?") && (init as RequestInit)?.method === "POST",
    );
    expect(aguiCall).toBeTruthy();
    const [url, init] = aguiCall!;
    const parsedUrl = new URL(String(url), "http://localhost");
    expect(parsedUrl.searchParams.get("agent_name")).toBe("archive-assistant-agent");
    expect(parsedUrl.searchParams.get("agent_version")).toBe("0.1.1");
    const body = JSON.parse(String((init as RequestInit).body));
    expect(body.threadId).toBe(threadId);
    expect(body.messages).toHaveLength(1);
    expect(body.messages[0].role).toBe("user");
    expect(body.messages[0].content).toContain("请确认参数");
    expect(body.messages[0].content).toContain("8 公里");

    // 成功后卡收起；没有跳到 ?bot=。
    expect(host.querySelector(".handover-ask-card")).toBeNull();
    expect(window.location.pathname + window.location.search).toBe("/");
  });

  it("renders nothing when the thread has no pending ask", async () => {
    historyMessages = [
      { id: "m1", role: "user", content: "随便聊聊" },
      { id: "m2", role: "assistant", content: "好的，都办完了。" },
    ];
    await mountWithHandover();
    expect(host.querySelector(".handover-ask-card")).toBeNull();
    expect(host.textContent ?? "").not.toContain("正在等你回答");
  });

  it("ignores an ask that the user already answered", async () => {
    historyMessages = [
      { id: "m1", role: "assistant", content: askMessage },
      { id: "m2", role: "user", content: "8 公里" },
    ];
    await mountWithHandover();
    expect(host.querySelector(".handover-ask-card")).toBeNull();
  });
});

it("keeps an actionable confirmation on its current source message without repeating it under old or unrelated answers", async () => {
  await mountWithHandover();
  await act(async () => {
    emitHandover({ handover_id: "older-request", run_id: "run-old", specialist_id: specialistId,
      specialist_name: "休息提醒", status: "applied", created_at: "2026-11-01T01:00:00Z", updated_at: "2026-11-03T01:00:00Z" });
    root!.render(<HandoverInlineRows messageId="assistant-run-old" />);
  });
  await flush();
  expect(host.textContent).toContain("已安排");
  expect(host.querySelector(".handover-ask-card")).toBeNull();
  await act(async () => root!.render(<HandoverInlineRows messageId="assistant-unrelated" />));
  expect(host.textContent).toBe("");
  await act(async () => root!.render(<HandoverInlineRows messageId="assistant-run-ui-test" />));
  await flush();
  expect(host.querySelector(".handover-ask-card")).not.toBeNull();
});
