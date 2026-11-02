// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { AssistantRuntimeProvider, ExportedMessageRepository, MessagePrimitive, ThreadPrimitive, useLocalRuntime, useThreadRuntime } from "@assistant-ui/react";
import { afterEach, expect, it, vi } from "vitest";
import { HarnessAssistantMessage, LiveStreamingBubble } from "../src/components/agent-thread";
import { ConversationSurfaceProvider, useAssistantSurface } from "../src/components/assistant-bot-context";
import { liveResponseStore } from "../src/lib/live-response-store";
import { activityStore } from "../src/lib/activity-store";
import { resetRuntimeThreadScope } from "../src/lib/runtime-thread-scope";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
vi.stubGlobal("localStorage", { getItem: () => null, setItem() {}, removeItem() {} });
let root: Root, host: HTMLDivElement, thread: ReturnType<typeof useThreadRuntime>;
function User() { return <><MessagePrimitive.Root />{useAssistantSurface() && <LiveStreamingBubble />}</>; }
function Capture() { thread = useThreadRuntime(); return null; }
function Fixture({surface = "assistant"}: {surface?: "assistant" | "task"}) {
  const runtime = useLocalRuntime({async *run() { yield {content: [{type: "text" as const, text: "unused"}]}; }});
  return <ConversationSurfaceProvider surface={surface}><AssistantRuntimeProvider runtime={runtime}>
    <Capture /><ThreadPrimitive.Root><ThreadPrimitive.Messages components={{AssistantMessage: HarnessAssistantMessage, UserMessage: User}} /></ThreadPrimitive.Root>
  </AssistantRuntimeProvider></ConversationSurfaceProvider>;
}
afterEach(async () => {
  if (root) await act(async () => root.unmount()); host?.remove();
  liveResponseStore.clear(); activityStore.clear(); resetRuntimeThreadScope();
});

it("never repeats a live reply after older user messages, even when cleaning shortens the formal reply", async () => {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  await act(async () => root.render(<Fixture />));
  const text = "「编码协同」仍是 `[needs_input]` 状态，无法自行执行。仅发 HEAD 请求，不带任何凭据。";
  const messages = [
    {id: "user-old", role: "user" as const, content: [{type: "text" as const, text: "旧问题"}]},
    {id: "assistant-old", role: "assistant" as const, content: [{type: "text" as const, text: "旧回答"}]},
    {id: "user-new", role: "user" as const, content: [{type: "text" as const, text: "请检查连通性"}]},
    {id: "assistant-live-status", role: "assistant" as const, content: [{type: "text" as const, text}]},
  ];
  await act(async () => {
    thread!.import(ExportedMessageRepository.fromArray(messages));
    activityStore.publish({run_id: "live-status", status: "running", started_at: "2026-11-02T12:00:00Z", items: [], metrics: {}});
    liveResponseStore.startRun("live-status"); liveResponseStore.startMessage("assistant-live-status");
    liveResponseStore.append("assistant-live-status", text); liveResponseStore.completeMessage("assistant-live-status");
  });
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 200)); });
  expect(host.querySelectorAll('[data-live-preview]')).toHaveLength(0);
  expect(host.querySelectorAll('.assistant-answer')).toHaveLength(2); // old answer + this reply
  expect(host.textContent?.match(/仅发 HEAD 请求/g)).toHaveLength(1);
  expect(host.textContent).not.toContain('needs_input');
  expect(host.textContent).not.toContain('`');
  expect(host.textContent).toContain('等你补充信息');
  await act(async () => {activityStore.publish({run_id: "live-status", status: "succeeded", started_at: "2026-11-02T12:00:00Z", items: [], metrics: {}}); liveResponseStore.completeRun(); thread!.import(ExportedMessageRepository.fromArray(messages)); liveResponseStore.clear();});
  expect(host.textContent?.match(/仅发 HEAD 请求/g)).toHaveLength(1);
  expect(host.querySelectorAll('[data-live-preview]')).toHaveLength(0);
});
