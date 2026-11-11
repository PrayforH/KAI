// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import {
  MemoryProposalCard, MemoryToolNotice, memoryToolFailure, parseMemoryProposal,
} from "../src/components/memory-proposal-card";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
const host = document.createElement("div");
document.body.append(host);
let root = createRoot(host);
afterEach(() => {
  act(() => root.unmount());
  host.replaceChildren();
  root = createRoot(host);
  vi.unstubAllGlobals();
});

const entry = {
  tenantId: "t", userId: "u", agentName: "lead-agent", entryId: "memory_1",
  content: "团队名单：张三、李四、王五", contentHash: "h", sensitivity: "personal",
  status: "pending", version: 2, confidence: 0.7,
  source: { sourceId: "s", kind: "agent", label: "Agent 提议", runId: null, sessionId: null, capturedAt: "" },
  consentId: null, createdAt: "", updatedAt: "", expiresAt: null, deletedAt: null,
};

const pendingProposal = {
  entryId: "memory_1", status: "pending" as const, requiresConfirmation: true,
};

type Handler = () => Promise<unknown>;

function stubMemoryApi(overrides: { list?: Handler; confirm?: Handler; reject?: Handler } = {}) {
  const calls = {
    list: vi.fn(overrides.list ?? (async () => [entry])),
    confirm: vi.fn(overrides.confirm ?? (async () => ({ ...entry, status: "active", version: 3 }))),
    reject: vi.fn(overrides.reject ?? (async () => ({ ...entry, status: "rejected", version: 3 }))),
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    const handler = url.includes("/confirm")
      ? calls.confirm
      : url.includes("/reject")
        ? calls.reject
        : calls.list;
    try {
      const payload = await handler();
      return { ok: true, status: 200, json: async () => payload };
    } catch (cause) {
      return {
        ok: false,
        status: 500,
        json: async () => ({
          error: { message: cause instanceof Error ? cause.message : "请求失败" },
        }),
      };
    }
  }));
  return calls;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(resolver => { resolve = resolver; });
  return { promise, resolve };
}

async function flush() {
  await act(async () => {
    await new Promise(resolve => setTimeout(resolve, 0));
  });
}

async function render(node: React.ReactNode) {
  await act(async () => { root.render(node); });
  await flush();
}

function button(label: string) {
  return [...host.querySelectorAll("button")].find(node => node.textContent === label);
}

async function click(label: string) {
  const target = button(label);
  expect(target, `按钮「${label}」`).toBeTruthy();
  await act(async () => { target!.click(); });
}

it("reads the tool's JSON from text or MCP content parts", () => {
  const payload = { entryId: "memory_1", status: "pending", requiresConfirmation: true };
  expect(parseMemoryProposal(JSON.stringify(payload)))
    .toEqual({ entryId: "memory_1", status: "pending", requiresConfirmation: true });
  expect(parseMemoryProposal({ content: [{ type: "text", text: JSON.stringify(payload) }] })?.entryId)
    .toBe("memory_1");
  expect(parseMemoryProposal({ entryId: "memory_2", status: "active" })?.requiresConfirmation)
    .toBe(false);
  expect(parseMemoryProposal("not json")).toBeNull();
  expect(parseMemoryProposal(null)).toBeNull();
});

it("explains a policy denial and a truncated submission", () => {
  expect(memoryToolFailure("PreToolUse hook error: matched policy rule untrusted-memory-deny"))
    .toBe("untrusted");
  expect(memoryToolFailure("InputValidationError: could not be parsed as JSON")).toBe("invalid");
  expect(memoryToolFailure("ok")).toBeNull();
});

it("state pending: shows the content, source, sensitivity and scope before confirming", async () => {
  stubMemoryApi();
  await render(<MemoryProposalCard proposal={pendingProposal} />);

  expect(host.textContent).toContain("待确认");
  expect(host.textContent).toContain("团队名单：张三、李四、王五");
  expect(host.textContent).toContain("助理整理");
  expect(host.textContent).toContain("个人记忆");
  expect(host.textContent).toContain("lead-agent");
  expect(button("确认保存")).toBeTruthy();
  expect(button("不保存")).toBeTruthy();
});

it("state done: confirms a pending proposal and reports the saved state", async () => {
  const api = stubMemoryApi();
  await render(<MemoryProposalCard proposal={pendingProposal} />);

  await click("确认保存");
  await flush();

  expect(api.confirm).toHaveBeenCalledTimes(1);
  expect(host.textContent).toContain("已生效");
  expect(host.textContent).toContain("已写入长期记忆");
  expect(button("确认保存")).toBeUndefined();
  expect(button("不保存")).toBeUndefined();
});

it("state rejected: rejects a pending proposal and reports the refused state", async () => {
  const api = stubMemoryApi();
  await render(<MemoryProposalCard proposal={pendingProposal} />);

  await click("不保存");
  await flush();

  expect(api.reject).toHaveBeenCalledTimes(1);
  expect(host.textContent).toContain("已拒绝");
  expect(host.textContent).toContain("已拒绝这条记忆");
  expect(button("确认保存")).toBeUndefined();
});

it("state confirming: distinguishes saving from rejecting and disables both actions", async () => {
  const pending = deferred<unknown>();
  stubMemoryApi({ confirm: () => pending.promise });
  await render(<MemoryProposalCard proposal={pendingProposal} />);

  await click("确认保存");

  expect(host.textContent).toContain("正在保存…");
  expect(host.textContent).toContain("正在保存");
  expect(button("不保存")).toBeTruthy();
  const actions = [...host.querySelectorAll("button")];
  expect(actions).toHaveLength(2);
  expect(actions.every(node => node.disabled)).toBe(true);
  expect(host.querySelector("section")?.getAttribute("aria-busy")).toBe("true");

  await act(async () => {
    pending.resolve({ ...entry, status: "active", version: 3 });
    await pending.promise;
  });
  await flush();
  expect(host.textContent).toContain("已写入长期记忆");
});

it("state rejecting: shows its own busy copy while the refusal is in flight", async () => {
  const pending = deferred<unknown>();
  stubMemoryApi({ reject: () => pending.promise });
  await render(<MemoryProposalCard proposal={pendingProposal} />);

  await click("不保存");

  expect(host.textContent).toContain("正在拒绝…");
  expect(host.textContent).toContain("正在拒绝");
  expect(button("确认保存")).toBeTruthy();
  const actions = [...host.querySelectorAll("button")];
  expect(actions).toHaveLength(2);
  expect(actions.every(node => node.disabled)).toBe(true);

  await act(async () => {
    pending.resolve({ ...entry, status: "rejected", version: 3 });
    await pending.promise;
  });
  await flush();
  expect(host.textContent).toContain("已拒绝这条记忆");
});

it("state error: reports a failed save with role=alert and retries the action", async () => {
  let attempt = 0;
  const api = stubMemoryApi({
    confirm: async () => {
      attempt += 1;
      if (attempt === 1) throw new Error("版本冲突");
      return { ...entry, status: "active", version: 3 };
    },
  });
  await render(<MemoryProposalCard proposal={pendingProposal} />);

  await click("确认保存");
  await flush();

  const alert = host.querySelector('[role="alert"]');
  expect(alert?.textContent).toContain("保存失败：版本冲突");
  expect(host.textContent).toContain("保存失败");
  expect(button("重试保存")).toBeTruthy();

  await click("重试保存");
  await flush();

  expect(api.confirm).toHaveBeenCalledTimes(2);
  expect(host.textContent).toContain("已写入长期记忆");
});

it("state error: keeps an unreadable entry actionable with a retry and settings link", async () => {
  stubMemoryApi({ list: async () => { throw new Error("记忆服务不可用"); } });
  await render(<MemoryProposalCard proposal={pendingProposal} />);

  expect(host.querySelector('[role="alert"]')?.textContent).toContain("记忆服务不可用");
  expect(host.textContent).toContain("读取失败");
  expect(host.textContent).toContain("内容暂时取不到");
  expect(button("重试读取")).toBeTruthy();
  expect(host.textContent).toContain("去记忆设置查看");
});

it("state not-found: explains a missing entry without leaving the card blank", async () => {
  stubMemoryApi({ list: async () => [] });
  await render(<MemoryProposalCard proposal={pendingProposal} />);

  expect(host.textContent).toContain("未写入");
  expect(host.textContent).toContain("内容暂时取不到");
  expect(host.textContent).toContain("去记忆设置查看");
  expect(button("重试读取")).toBeTruthy();
});

it("offers expand and collapse for a long memory body", async () => {
  const content = "这是一段很长的长期记忆内容。".repeat(20);
  stubMemoryApi({ list: async () => [{ ...entry, content }] });
  await render(<MemoryProposalCard proposal={pendingProposal} />);

  const expand = button("展开全文");
  expect(expand).toBeTruthy();
  expect(expand!.getAttribute("aria-expanded")).toBe("false");

  await act(async () => { expand!.click(); });

  const collapse = button("收起");
  expect(collapse).toBeTruthy();
  expect(collapse!.getAttribute("aria-expanded")).toBe("true");
});

it("shows an active proposal as already saved", async () => {
  stubMemoryApi();
  await render(<MemoryProposalCard proposal={{
    entryId: "memory_1", status: "active", requiresConfirmation: false,
  }} />);

  expect(host.textContent).toContain("已写入长期记忆");
  expect(host.querySelector("button")).toBeNull();
});

it("tells the user why an untrusted run wrote nothing", async () => {
  await render(<MemoryToolNotice reason="untrusted" />);
  expect(host.textContent).toContain("外部内容");
  expect(host.textContent).toContain("记忆未写入");
});
