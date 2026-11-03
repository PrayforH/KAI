import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { submitThreadAnswer } from "../src/lib/thread-answer";

let bodies: unknown[] = [];

beforeEach(() => {
  bodies = [];
  vi.stubGlobal("fetch", vi.fn(async (_url: string, init?: RequestInit) => {
    bodies.push(JSON.parse(String(init?.body ?? "{}")));
    return new Response("", { status: 200 });
  }));
});

afterEach(() => vi.unstubAllGlobals());

describe("投递给助理线程的消息", () => {
  it("carries the assistant scope so the run can inherit platform web", async () => {
    // 被 @ 的 Bot 走这条通道；漏了 assistantScope，运行时就不会装配平台联网，
    // 于是只能回「取不到」。
    await submitThreadAnswer({
      threadId: "assistant_specialist_demo",
      agentName: "archive-assistant-agent",
      agentVersion: "0.1.1",
      text: "今天上海天气",
    });
    const body = bodies[0] as { forwardedProps?: Record<string, unknown> } | undefined;
    expect(body?.forwardedProps).toEqual({
      conversationSurface: "assistant",
      assistantScope: true,
    });
  });
});
