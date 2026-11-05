// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import type { RunActivity } from "../src/lib/activity-schema";
import { ConversationSurfaceProvider } from "../src/components/assistant-bot-context";
import { AssistantConversationTail } from "../src/components/assistant-conversation-tail";
import { PromptQueue } from "../src/components/prompt-queue";
import { handoverStore } from "../src/lib/handover-store";
const state = vi.hoisted(() => ({ activity: undefined as RunActivity | undefined,
  messages: [] as Array<{id: string}> }));
vi.mock("@assistant-ui/react", () => ({
  useAuiState: (select: (value: unknown) => unknown) => select({thread: {messages: state.messages}}),
}));
vi.mock("../src/lib/activity-store", () => ({
  useRunActivity: () => state.activity,
  useRunViewModel: () => ({ runId: state.activity?.run_id }),
}));
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root;
let host: HTMLDivElement;
afterEach(() => { act(() => root?.unmount()); host?.remove(); });
it("puts exactly one live status after the newest message as more messages arrive and removes it on completion", () => {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  state.activity = { run_id: "r1", status: "running", started_at: new Date().toISOString(), metrics: {}, items: [{ id: "start", sequence: 1, timestamp: new Date().toISOString(), event_type: "run.running", kind: "run", status: "running", title: "开始运行", metadata: {} }] };
  const render = (texts: string[]) => act(() => root.render(<ConversationSurfaceProvider surface="assistant">
    <div className="harness-thread-frame"><div data-assistant-message-tail /><div className="aui-thread-viewport-footer">
    <AssistantConversationTail><PromptQueue chat items={texts.map((text, i) => ({id: String(i), text, attachments: []}))}
      paused={false} busy canSteer sendingIds={[]} onChange={vi.fn()} onPause={vi.fn()} onGuide={vi.fn()} onSend={vi.fn()} /></AssistantConversationTail></div></div>
  </ConversationSurfaceProvider>));
  render(["检查电脑"]);
  render(["检查电脑", "帮我恢复下"]);
  const bubble = host.querySelectorAll(".steering-user-message-bubble")[1];
  const progress = host.querySelector(".grok-activity-row");
  expect(progress).not.toBeNull();
  expect(host.querySelector(".aui-thread-viewport-footer")?.textContent).toBe("");
  expect(host.querySelector("[data-assistant-message-tail]")?.contains(bubble)).toBe(true);
  expect(bubble.compareDocumentPosition(progress!)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  expect(host.querySelectorAll(".grok-activity-row")).toHaveLength(1);
  expect(host.textContent).not.toMatch(/等待接收|重新发送|接收状态待核对|模型尚未返回/);
  state.activity = { ...state.activity!, status: "succeeded" };
  render(["检查电脑", "帮我恢复下"]);
  expect(host.querySelector(".grok-activity-row")).toBeNull();
});

it("moves a retained legacy bubble beside its original turn instead of pinning it to the composer", async () => {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  const { PendingMessagePlacement } = await import("../src/components/pending-message-placement");
  act(() => root.render(<div className="harness-thread-frame">
    <article data-turn-id="assistant-original-run" />
    <div className="aui-thread-viewport-footer"><PendingMessagePlacement runId="original-run">
      <span>帮我恢复下</span>
    </PendingMessagePlacement></div>
  </div>));
  expect(host.querySelector("[data-turn-id]")?.textContent).toBe("帮我恢复下");
  expect(host.querySelector(".aui-thread-viewport-footer")?.textContent).toBe("");
});

it("does not pin an old retained send to the composer when its history page is absent", async () => {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  const { PendingMessagePlacement } = await import("../src/components/pending-message-placement");
  const render = (loaded: boolean) => act(() => root.render(<div className="harness-thread-frame">
    {loaded && <article data-turn-id="assistant-old-run" />}
    <article data-turn-id="assistant-new-run" />
    <div className="aui-thread-viewport-footer"><PendingMessagePlacement runId="old-run">
      <span>我说的是微信通道啊</span>
    </PendingMessagePlacement></div>
  </div>));
  render(false);
  expect(host.textContent).toBe("");
  render(true);
  await act(async () => { await Promise.resolve(); });
  expect(host.querySelector('[data-turn-id="assistant-old-run"]')?.textContent).toBe("我说的是微信通道啊");
  expect(host.querySelector(".aui-thread-viewport-footer")?.textContent).toBe("");
  render(false);
  await act(async () => { await Promise.resolve(); });
  expect(host.textContent).toBe("");
});

it("hands a live send over to its durable position before the child reply without a footer duplicate", () => {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  state.activity = undefined;
  state.messages = [];
  handoverStore.ingestMention({handoverId: "tail-handoff", specialistId: "bot",
    specialistName: "资料 Bot", sourceThreadId: "origin", runId: "child", text: "检查资料"});
  const render = () => act(() => root.render(<ConversationSurfaceProvider surface="assistant">
    <div className="harness-thread-frame">
      {state.messages.length > 0 && <><p>检查资料</p><p>已发送消息给 资料 Bot</p><p>检查完成</p></>}
      <div data-assistant-message-tail /><AssistantConversationTail threadId="origin">{null}</AssistantConversationTail>
    </div>
  </ConversationSurfaceProvider>));
  render();
  expect(host.querySelectorAll(".assistant-mention-row")).toHaveLength(1);
  state.messages = [{id: "mention-sent-tail-handoff"}, {id: "mention-result-tail-handoff"}];
  render();
  expect(host.querySelector(".assistant-mention-row")).toBeNull();
  expect(host.textContent).toBe("检查资料已发送消息给 资料 Bot检查完成");
  render(); // Reconciliation stays idempotent.
  expect(host.textContent).toBe("检查资料已发送消息给 资料 Bot检查完成");
});
