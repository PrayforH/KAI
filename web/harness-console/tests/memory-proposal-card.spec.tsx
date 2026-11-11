// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import {
  MEMORY_PROPOSAL_CACHE_LIMIT, MemoryProposalCard, MemoryToolNotice,
  clearMemoryProposalCache, memoryProposalCacheSize, memoryToolFailure, parseMemoryProposal,
} from "../src/components/memory-proposal-card";
import { MemoryApiError, memoryClient, type MemoryEntry } from "../src/lib/memory-client";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
const host = document.createElement("div");
document.body.append(host);
let root = createRoot(host);
afterEach(() => {
  act(() => root.unmount());
  host.replaceChildren();
  root = createRoot(host);
  clearMemoryProposalCache();
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

class ApiFailure {
  constructor(readonly status: number, readonly code: string, readonly message: string) {}
}

function apiFailure(status: number, code: string, message: string) {
  return new ApiFailure(status, code, message);
}

function stubMemoryApi(overrides: { list?: Handler; confirm?: Handler; reject?: Handler } = {}) {
  const bodies: unknown[] = [];
  const calls = {
    list: vi.fn(overrides.list ?? (async () => [entry])),
    confirm: vi.fn(overrides.confirm ?? (async () => ({ ...entry, status: "active", version: 3 }))),
    reject: vi.fn(overrides.reject ?? (async () => ({ ...entry, status: "rejected", version: 3 }))),
  };
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (init?.body) bodies.push(JSON.parse(String(init.body)));
    const handler = url.includes("/confirm")
      ? calls.confirm
      : url.includes("/reject")
        ? calls.reject
        : calls.list;
    try {
      const payload = await handler();
      return { ok: true, status: 200, json: async () => payload };
    } catch (cause) {
      if (cause instanceof ApiFailure) {
        return {
          ok: false,
          status: cause.status,
          json: async () => ({ error: { code: cause.code, message: cause.message } }),
        };
      }
      return {
        ok: false,
        status: 500,
        json: async () => ({
          error: { message: cause instanceof Error ? cause.message : "请求失败" },
        }),
      };
    }
  }));
  return { ...calls, bodies };
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
  expect(host.textContent).toContain("内容没有读到");
  expect(host.textContent).toContain("这不代表没有写入");
  expect(button("重试读取")).toBeTruthy();
  expect(host.textContent).toContain("去记忆设置查看");
});

it("state not-found: explains a missing entry without leaving the card blank", async () => {
  stubMemoryApi({ list: async () => [] });
  await render(<MemoryProposalCard proposal={pendingProposal} />);

  expect(host.textContent).toContain("未找到");
  expect(host.textContent).toContain("没有找到这条记忆");
  expect(host.textContent).toContain("不在当前列表中");
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

it("state loading: keeps the reading state while the entry request is in flight", async () => {
  const pending = deferred<unknown>();
  stubMemoryApi({ list: () => pending.promise });
  await act(async () => {
    root.render(<MemoryProposalCard proposal={pendingProposal} />);
  });

  expect(host.textContent).toContain("读取中");
  expect(host.textContent).toContain("正在读取这条记忆的内容…");
  expect(host.querySelectorAll("button")).toHaveLength(0);

  await act(async () => {
    pending.resolve([entry]);
    await pending.promise;
  });
  await flush();
  expect(host.textContent).toContain("待确认");
});

it("state error: retries a failed refusal instead of dropping it", async () => {
  let attempt = 0;
  const api = stubMemoryApi({
    reject: async () => {
      attempt += 1;
      if (attempt === 1) throw new Error("拒绝请求失败");
      return { ...entry, status: "rejected", version: 3 };
    },
  });
  await render(<MemoryProposalCard proposal={pendingProposal} />);

  await click("不保存");
  await flush();

  expect(host.textContent).toContain("拒绝失败");
  expect(host.querySelector('[role="alert"]')?.textContent).toContain("拒绝失败：拒绝请求失败");
  expect(button("重试拒绝")).toBeTruthy();

  await click("重试拒绝");
  await flush();

  expect(api.reject).toHaveBeenCalledTimes(2);
  expect(host.textContent).toContain("已拒绝这条记忆");
});

it("state conflict: re-reads the latest version after a 409 and confirms with fresh data", async () => {
  let listCalls = 0;
  let confirmCalls = 0;
  const api = stubMemoryApi({
    list: async () => {
      listCalls += 1;
      return listCalls === 1 ? [entry] : [{ ...entry, version: 3 }];
    },
    confirm: async () => {
      confirmCalls += 1;
      if (confirmCalls === 1) {
        throw apiFailure(409, "conflict", "memory entry changed or is not pending");
      }
      return { ...entry, status: "active", version: 4 };
    },
  });
  await render(<MemoryProposalCard proposal={pendingProposal} />);

  await click("确认保存");
  await flush();
  await flush();

  expect(api.confirm).toHaveBeenCalledTimes(1);
  expect(listCalls).toBe(2);
  expect(host.textContent).toContain("已为你刷新");
  expect(host.textContent).not.toContain("memory entry changed");
  expect(host.textContent).toContain("待确认");
  expect(button("确认保存")).toBeTruthy();

  await click("确认保存");
  await flush();

  expect(api.confirm).toHaveBeenCalledTimes(2);
  const versions = api.bodies.map(body => (body as { expectedVersion?: number }).expectedVersion);
  expect(versions).toEqual([2, 3]);
  expect(host.textContent).toContain("已写入长期记忆");
});

it("state done: invalidates the cached read so a remount shows the saved state", async () => {
  let confirmed = false;
  const api = stubMemoryApi({
    list: async () => [confirmed ? { ...entry, status: "active", version: 3 } : entry],
    confirm: async () => {
      confirmed = true;
      return { ...entry, status: "active", version: 3 };
    },
  });
  await render(<MemoryProposalCard proposal={pendingProposal} />);
  expect(host.textContent).toContain("待确认");

  await click("确认保存");
  await flush();
  expect(host.textContent).toContain("已写入长期记忆");

  await act(async () => { root.render(<div />); });
  await render(<MemoryProposalCard proposal={pendingProposal} />);

  expect(api.list).toHaveBeenCalledTimes(2);
  expect(host.textContent).toContain("已写入长期记忆");
  expect(host.textContent).not.toContain("待确认");
});

it("state blocked: a changed replacement target stops instead of retrying the same version", async () => {
  const api = stubMemoryApi({
    confirm: async () => {
      throw apiFailure(409, "conflict", "replacement target changed; review the suggestion again");
    },
  });
  await render(<MemoryProposalCard proposal={pendingProposal} />);

  await click("确认保存");
  await flush();
  await flush();

  expect(api.confirm).toHaveBeenCalledTimes(1);
  expect(host.textContent).toContain("替换目标已变更");
  expect(host.textContent).toContain("无法保存");
  expect(host.textContent).not.toContain("已为你刷新");
  expect(host.textContent).not.toContain("replacement target changed");
  expect(button("确认保存")).toBeUndefined();
  expect(button("不保存")).toBeTruthy();
  expect(api.bodies.map(body => (body as { expectedVersion?: number }).expectedVersion))
    .toEqual([2]);

  // No re-read turns this back into a pending retry with the same version.
  await flush();
  expect(api.list).toHaveBeenCalledTimes(1);
  expect(button("确认保存")).toBeUndefined();

  // Rejecting is the terminal exit, and it is still allowed.
  await click("不保存");
  await flush();
  expect(api.reject).toHaveBeenCalledTimes(1);
  expect(host.textContent).toContain("已拒绝这条记忆");
});

it("state conflict: re-reads after a 409 while rejecting and retries with the fresh version", async () => {
  let listCalls = 0;
  let rejectCalls = 0;
  const api = stubMemoryApi({
    list: async () => {
      listCalls += 1;
      return listCalls === 1 ? [entry] : [{ ...entry, version: 3 }];
    },
    reject: async () => {
      rejectCalls += 1;
      if (rejectCalls === 1) {
        throw apiFailure(409, "conflict", "memory entry changed or is not pending");
      }
      return { ...entry, status: "rejected", version: 4 };
    },
  });
  await render(<MemoryProposalCard proposal={pendingProposal} />);

  await click("不保存");
  await flush();
  await flush();

  expect(api.reject).toHaveBeenCalledTimes(1);
  expect(listCalls).toBe(2);
  expect(host.textContent).toContain("已为你刷新");
  expect(host.textContent).toContain("请重新选择");
  expect(host.textContent).not.toContain("memory entry changed");
  expect(button("不保存")).toBeTruthy();

  await click("不保存");
  await flush();

  expect(api.reject).toHaveBeenCalledTimes(2);
  const versions = api.bodies.map(body => (body as { expectedVersion?: number }).expectedVersion);
  expect(versions).toEqual([2, 3]);
  expect(host.textContent).toContain("已拒绝这条记忆");
});

it("keeps the HTTP status and error code on a failed request", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ({
    ok: false,
    status: 409,
    json: async () => ({
      error: { code: "conflict", message: "memory entry changed or is not pending" },
    }),
  })));

  const failure = await memoryClient
    .confirm(entry as unknown as MemoryEntry)
    .catch((cause: unknown) => cause);

  expect(failure).toBeInstanceOf(MemoryApiError);
  expect((failure as MemoryApiError).status).toBe(409);
  expect((failure as MemoryApiError).code).toBe("conflict");
  expect((failure as MemoryApiError).message).toBe("memory entry changed or is not pending");
});

it("state error: drops the previous entry when a replaced proposal cannot be read", async () => {
  let failing = false;
  stubMemoryApi({
    list: async () => {
      if (failing) throw new Error("记忆服务不可用");
      return [entry];
    },
  });
  await render(<MemoryProposalCard proposal={pendingProposal} />);
  expect(host.textContent).toContain("团队名单：张三、李四、王五");

  failing = true;
  await act(async () => {
    root.render(<MemoryProposalCard proposal={{
      entryId: "memory_2", status: "pending", requiresConfirmation: true,
    }} />);
  });
  await flush();

  expect(host.textContent).not.toContain("团队名单：张三、李四、王五");
  expect(host.textContent).toContain("内容没有读到");
  expect(button("重试读取")).toBeTruthy();
});

it("shares one entries read across cards mounted together", async () => {
  const second = { ...entry, entryId: "memory_2", content: "第二条记忆" };
  const api = stubMemoryApi({ list: async () => [entry, second] });
  await act(async () => {
    root.render(
      <div>
        <MemoryProposalCard proposal={pendingProposal} />
        <MemoryProposalCard proposal={{
          entryId: "memory_2", status: "pending", requiresConfirmation: true,
        }} />
      </div>,
    );
  });
  await flush();

  expect(api.list).toHaveBeenCalledTimes(1);
  expect(host.textContent).toContain("团队名单：张三、李四、王五");
  expect(host.textContent).toContain("第二条记忆");
});

it("bounds the entry cache and re-reads an evicted entry", async () => {
  const entries = Array.from({ length: MEMORY_PROPOSAL_CACHE_LIMIT + 2 }, (_, index) => ({
    ...entry,
    entryId: `memory_${index}`,
    content: `记忆 ${index}`,
  }));
  const api = stubMemoryApi({ list: async () => entries });
  const proposalFor = (entryId: string) => ({
    entryId, status: "pending" as const, requiresConfirmation: true,
  });

  await act(async () => {
    root.render(
      <div>
        {entries.map(item => (
          <MemoryProposalCard key={item.entryId} proposal={proposalFor(item.entryId)} />
        ))}
      </div>,
    );
  });
  await flush();

  expect(memoryProposalCacheSize()).toBe(MEMORY_PROPOSAL_CACHE_LIMIT);
  expect(api.list).toHaveBeenCalledTimes(1);

  // The two oldest ids were evicted, so revisiting one reads the list again.
  act(() => root.render(<div />));
  await flush();
  await act(async () => {
    root.render(<MemoryProposalCard proposal={proposalFor(entries[0].entryId)} />);
  });
  await flush();
  expect(api.list).toHaveBeenCalledTimes(2);
  expect(memoryProposalCacheSize()).toBe(MEMORY_PROPOSAL_CACHE_LIMIT);

  // The newest id is still cached, so revisiting it makes no new request.
  act(() => root.render(<div />));
  await flush();
  await act(async () => {
    root.render(<MemoryProposalCard proposal={proposalFor(entries[entries.length - 1].entryId)} />);
  });
  await flush();
  expect(api.list).toHaveBeenCalledTimes(2);
});

it("tells the user why an untrusted run wrote nothing", async () => {
  await render(<MemoryToolNotice reason="untrusted" />);
  expect(host.textContent).toContain("外部内容");
  expect(host.textContent).toContain("记忆未写入");
});
