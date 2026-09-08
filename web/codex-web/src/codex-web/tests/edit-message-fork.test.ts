import { readFileSync } from "node:fs";

import { describe, expect, it, vi } from "vitest";

import { forkAndSendEditedMessage } from "../edit-message-fork";

const provider = readFileSync(new URL("../AppServerProvider.tsx", import.meta.url), "utf8");

describe("编辑消息 fork 编排", () => {
  it("Provider 原样发送 beforeTurnId", () => {
    expect(provider).toContain("beforeTurnId?: string | null");
    expect(provider).toContain("beforeTurnId: beforeTurnId ?? null");
  });

  it("严格按 fork、发送、导航顺序执行", async () => {
    const calls: string[] = [];
    const fork = vi.fn(async () => {
      calls.push("fork");
      return { threadId: "child-1" };
    });
    const send = vi.fn(async (threadId: string) => {
      calls.push(`send:${threadId}`);
    });
    const navigate = vi.fn((threadId: string) => {
      calls.push(`navigate:${threadId}`);
    });

    await forkAndSendEditedMessage({ fork, send, navigate });

    expect(calls).toEqual(["fork", "send:child-1", "navigate:child-1"]);
  });

  it("fork 失败时不发送也不跳转", async () => {
    const send = vi.fn();
    const navigate = vi.fn();

    await expect(forkAndSendEditedMessage({
      fork: async () => { throw new Error("fork failed"); },
      send,
      navigate,
    })).rejects.toThrow("fork failed");

    expect(send).not.toHaveBeenCalled();
    expect(navigate).not.toHaveBeenCalled();
  });

  it("发送失败时不跳转", async () => {
    const navigate = vi.fn();

    await expect(forkAndSendEditedMessage({
      fork: async () => ({ threadId: "child-1" }),
      send: async () => { throw new Error("send failed"); },
      navigate,
    })).rejects.toThrow("send failed");

    expect(navigate).not.toHaveBeenCalled();
  });
});
