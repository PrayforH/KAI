// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { AssistantRuntimeProvider, ThreadPrimitive, useAuiState } from "@assistant-ui/react";
import { useAgUiRuntime } from "@assistant-ui/react-ag-ui";
import { HttpAgent } from "@ag-ui/client";
import { ExportedMessageRepository } from "@assistant-ui/core";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { DurableHistorySync } from "../src/components/durable-history-sync";
import { activateRuntimeThread } from "../src/lib/runtime-thread-scope";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
const threadId = "kai-personal-assistant-v1";
const messages = [
  { id: "user-1", role: "user" as const, content: "你好" },
  { id: "answer-1", role: "assistant" as const, content: "你好！" },
  { id: "user-2", role: "user" as const, content: "继续聊" },
  { id: "answer-2", role: "assistant" as const, content: "可以" },
  { id: "external-user", role: "user" as const, content: "从飞书接着聊" },
  { id: "external-answer", role: "assistant" as const, content: "已经同步" },
];
let snapshot = ExportedMessageRepository.fromArray(messages.slice(0, 2));
const agent = new HttpAgent({ url: "http://127.0.0.1:1/unused" });
agent.threadId = threadId;
const history = {
  load: async () => snapshot,
  append: async () => {},
  loadSnapshot: async (accept?: (value: typeof snapshot) => void) => {
    accept?.(snapshot);
    return snapshot;
  },
};
function Message() {
  const text = useAuiState(s => s.message.content.map(p => p.type === "text" ? p.text : "").join(""));
  return <p>{text}</p>;
}
function App() {
  const runtime = useAgUiRuntime({ agent, adapters: { history } });
  return <AssistantRuntimeProvider runtime={runtime}>
    <DurableHistorySync threadId={threadId} revision={0} history={history as never} />
    <ThreadPrimitive.Messages components={{ Message }} />
  </AssistantRuntimeProvider>;
}
let root: Root;
let host: HTMLDivElement;
let errors: unknown[];
beforeEach(() => {
  vi.useFakeTimers(); activateRuntimeThread(threadId);
  snapshot = ExportedMessageRepository.fromArray(messages.slice(0, 2));
  host = document.createElement("div"); document.body.appendChild(host);
  errors = []; root = createRoot(host, { onUncaughtError: error => errors.push(error) });
});
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.useRealTimers(); });
it("keeps actual message clients consistent when idle history gains external turns", async () => {
  await act(async () => root.render(<App />));
  await act(async () => { await vi.advanceTimersByTimeAsync(20); });
  expect(host.textContent).toContain("你好！");
  for (const count of [4, 2, 4, 5, 6]) {
    snapshot = ExportedMessageRepository.fromArray(messages.slice(0, count));
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(errors).toEqual([]);
    expect(host.querySelectorAll("p")).toHaveLength(count);
  }
  expect(host.textContent).toContain("已经同步");
});
