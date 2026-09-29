// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { AssistantRuntimeProvider, ExportedMessageRepository, MessagePrimitive, ThreadPrimitive, useLocalRuntime, useThreadRuntime } from "@assistant-ui/react";
import { afterEach, expect, it, vi } from "vitest";
import { HarnessAssistantMessage } from "../src/components/agent-thread";
import { liveResponseStore } from "../src/lib/live-response-store";
import { resetRuntimeThreadScope } from "../src/lib/runtime-thread-scope";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
vi.stubGlobal("localStorage", { getItem: () => null, setItem() {}, removeItem() {} });
let root: Root;
let host: HTMLDivElement;
let thread: ReturnType<typeof useThreadRuntime>;

function Fixture() {
  const runtime = useLocalRuntime({ async *run() { yield { content: [{ type: "text" as const, text: "unused" }] }; } });
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <CaptureThread />
      <ThreadPrimitive.Root>
        <ThreadPrimitive.Messages components={{ AssistantMessage: HarnessAssistantMessage, UserMessage: MessagePrimitive.Root }} />
      </ThreadPrimitive.Root>
    </AssistantRuntimeProvider>
  );
}

function CaptureThread() {
  thread = useThreadRuntime();
  return null;
}

afterEach(async () => {
  if (root) await act(async () => root.unmount());
  host?.remove();
  liveResponseStore.clear();
  resetRuntimeThreadScope();
});

it("keeps a completed short answer visible after live ownership clears", async () => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => root.render(<Fixture />));

  await act(async () => thread!.import(ExportedMessageRepository.fromArray([
    { id: "assistant-run-1", role: "assistant", content: [{ type: "text", text: "OK" }] },
  ])));

  const assistant = host.querySelector('.harness-assistant-message[data-turn-answer="OK"]');
  expect(assistant?.querySelectorAll(".assistant-answer")).toHaveLength(1);
  expect(assistant?.querySelector(".assistant-answer")?.textContent).toBe("OK");
});

it("keeps only the final text when durable activity follows an operational tool", async () => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => root.render(<Fixture />));

  await act(async () => thread!.import(ExportedMessageRepository.fromArray([
    {
      id: "assistant-run-tools",
      role: "assistant",
      content: [
        { type: "text", text: "正在检查配置" },
        { type: "tool-call", toolCallId: "read-1", toolName: "Read", args: {}, result: "ok" },
        { type: "text", text: "## 最终结果\n\n已完成" },
        { type: "tool-call", toolCallId: "activity-1", toolName: "harness_run_activity", args: { activity: {} }, result: "ok" },
      ],
    },
  ])));

  const assistant = host.querySelector('.harness-assistant-message');
  expect(assistant?.getAttribute("data-turn-answer")).toBe("## 最终结果 已完成");
  expect(assistant?.querySelectorAll(".assistant-answer")).toHaveLength(1);
  expect(assistant?.querySelector(".assistant-answer")?.textContent).toContain("最终结果");
  expect(assistant?.querySelector(".assistant-answer")?.textContent).not.toContain("正在检查配置");
});

it("preserves the actual answer DOM across completion and history handoff", async () => {
  host = document.createElement("div"); document.body.appendChild(host); root = createRoot(host);
  await act(async () => root.render(<Fixture />));
  const answer = { id: "assistant-run-1", role: "assistant" as const, content: [{ type: "text" as const, text: "A stable answer. ".repeat(12) }] };
  await act(async () => {
    thread!.import(ExportedMessageRepository.fromArray([answer]));
    liveResponseStore.startRun("run-1"); liveResponseStore.startMessage(answer.id);
    liveResponseStore.append(answer.id, answer.content[0].text);
    liveResponseStore.completeMessage(answer.id);
  });
  const node = host.querySelector(".assistant-answer");
  const paragraph = node?.querySelector("p");
  expect(node?.textContent).toBe(answer.content[0].text.trim());
  await act(async () => liveResponseStore.completeRun());
  expect(host.querySelector(".assistant-answer")).toBe(node);
  expect(host.querySelector(".assistant-answer p")).toBe(paragraph);
  await act(async () => {
    thread!.import(ExportedMessageRepository.fromArray([answer]));
    liveResponseStore.clear();
  });
  expect(host.querySelector(".assistant-answer")).toBe(node);
  expect(host.querySelector(".assistant-answer p")).toBe(paragraph);
  expect(node?.textContent).toBe(answer.content[0].text.trim());
  await act(async () => liveResponseStore.startRun("run-2"));
  expect(host.querySelector(".assistant-answer")).toBe(node);
  expect(node?.textContent).toBe(answer.content[0].text.trim());
});
