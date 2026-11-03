// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import type { RunActivity } from "../src/lib/activity-schema";
import { ConversationSurfaceProvider } from "../src/components/assistant-bot-context";
import { AssistantConversationTail, AssistantSteeringMessages } from "../src/components/assistant-conversation-tail";
import { PromptQueue } from "../src/components/prompt-queue";
const state = vi.hoisted(() => ({ activity: undefined as RunActivity | undefined }));
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
    <AssistantConversationTail><PromptQueue chat items={texts.map((text, i) => ({id: String(i), text, attachments: []}))}
      paused={false} busy canSteer sendingIds={[]} onChange={vi.fn()} onPause={vi.fn()} onGuide={vi.fn()} onSend={vi.fn()} /></AssistantConversationTail>
  </ConversationSurfaceProvider>));
  render(["检查电脑"]);
  render(["检查电脑", "帮我恢复下"]);
  const bubble = host.querySelectorAll(".steering-user-message-bubble")[1];
  const progress = host.querySelector(".grok-activity-row");
  expect(progress).not.toBeNull();
  expect(bubble.compareDocumentPosition(progress!)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
  expect(host.querySelectorAll(".grok-activity-row")).toHaveLength(1);
  expect(host.textContent).not.toMatch(/等待接收|重新发送|接收状态待核对|模型尚未返回/);
  state.activity = { ...state.activity!, status: "succeeded" };
  render(["检查电脑", "帮我恢复下"]);
  expect(host.querySelector(".grok-activity-row")).toBeNull();
});
it("retains accepted and unknown messages in history once, without exposing receipt prose", () => {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  const activity: RunActivity = { run_id: "r", status: "succeeded", started_at: "2026-11-03T01:00:00Z", metrics: {}, items: [
    ...["requested", "accepted", "accepted"].map((status, i) => ({ id: String(i), sequence: i, timestamp: "2026-11-03T01:00:00Z", event_type: `run.steer.${status}`, kind: "analysis" as const, status: "succeeded" as const, title: "", summary: "保留原目录", metadata: {request_id: "m1"} })),
  ] };
  act(() => root.render(<AssistantSteeringMessages activity={activity} />));
  expect(host.textContent).toBe("保留原目录");
  expect(host.querySelectorAll(".steering-user-message-bubble")).toHaveLength(1);
});
