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
