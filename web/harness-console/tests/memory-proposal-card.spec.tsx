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
  content: "团队名单", contentHash: "h", sensitivity: "personal", status: "pending",
  version: 2, confidence: 0.7,
  source: { sourceId: "s", kind: "agent", label: "Agent 提议", runId: null, sessionId: null, capturedAt: "" },
  consentId: null, createdAt: "", updatedAt: "", expiresAt: null, deletedAt: null,
};

function stubMemoryApi() {
  const confirm = vi.fn(async () => ({ ...entry, status: "active", version: 3 }));
  vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes("/confirm")) return { ok: true, status: 200, json: confirm };
    return { ok: true, status: 200, json: async () => [entry] };
  }));
  return confirm;
}

async function render(node: React.ReactNode) {
  await act(async () => { root.render(node); await Promise.resolve(); });
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

it("confirms a pending proposal from the chat", async () => {
  const confirm = stubMemoryApi();
  await render(<MemoryProposalCard proposal={{
    entryId: "memory_1", status: "pending", requiresConfirmation: true,
  }} />);
  expect(host.textContent).toContain("记忆待确认");
  await act(async () => {
    [...host.querySelectorAll("button")].find(item => item.textContent === "确认保存")!.click();
  });
  await act(async () => { await Promise.resolve(); });
  expect(confirm).toHaveBeenCalled();
  expect(host.textContent).toContain("已写入长期记忆");
});

it("shows an active proposal as already saved", async () => {
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
