import { afterEach, expect, it, vi } from "vitest";
import { assistantRequest } from "../src/lib/assistant-client";
afterEach(() => vi.unstubAllGlobals());
it("presents a short conversation conflict without exposing thread or runtime identifiers", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({error: {message:
    "AG-UI thread assistant_objective_private is already bound to lead-agent@1.1.1+platform.private"}}), {status: 409})));
  await expect(assistantRequest("specialists/bot/messages", "POST", {text: "在吗"}))
    .rejects.toThrow("这个助理的会话已更新，请重新进入后再发送。");
});
it("preserves actionable validation errors", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({error: {message: "这个 Bot 已暂停接单，先恢复再发消息"}}), {status: 409})));
  await expect(assistantRequest("specialists/bot/messages", "POST", {text: "在吗"}))
    .rejects.toThrow("这个 Bot 已暂停接单");
});
