// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import {
  AssistantRuntimeProvider,
  ExportedMessageRepository,
  ThreadPrimitive,
  useLocalRuntime,
  useThreadRuntime,
} from "@assistant-ui/react";
import { afterEach, expect, it, vi } from "vitest";
import { HarnessAssistantMessage } from "../src/components/agent-thread";
import { ConversationSurfaceProvider } from "../src/components/assistant-bot-context";
import { resetRuntimeThreadScope } from "../src/lib/runtime-thread-scope";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));
vi.stubGlobal("localStorage", { getItem: () => null, setItem() {}, removeItem() {} });

let root: Root | undefined;
let host: HTMLDivElement | undefined;
let thread: ReturnType<typeof useThreadRuntime>;

function Capture() {
  thread = useThreadRuntime();
  return null;
}

function Fixture({ surface }: { surface: "assistant" | "task" }) {
  const runtime = useLocalRuntime({ async *run() { yield { content: [{ type: "text" as const, text: "unused" }] }; } });
  return (
    <ConversationSurfaceProvider surface={surface}>
      <AssistantRuntimeProvider runtime={runtime}>
        <Capture />
        <ThreadPrimitive.Root>
          <ThreadPrimitive.Messages components={{ AssistantMessage: HarnessAssistantMessage, UserMessage: () => null }} />
        </ThreadPrimitive.Root>
      </AssistantRuntimeProvider>
    </ConversationSurfaceProvider>
  );
}

/** A block the console accepts; the fallback sentence is what the screenshot showed. */
const presentUi = (fallback: string) => ({
  schema_version: "harness.ui/v1",
  presentation_id: "ui_1",
  revision: 1,
  run_id: "run-1",
  message_id: "assistant-run-1",
  placement: "inline",
  kind: "summary",
  renderer_version: "summary/1",
  title: null,
  fallback_text: fallback,
  data: { items: [{ label: "周报条目", value: "19" }] },
  config: {},
  decision: { origin: "model" },
});

async function renderWithPresentation(surface: "assistant" | "task", fallback: string) {
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
  await act(async () => root!.render(<Fixture surface={surface} />));
  const messages = [
    { id: "user-1", role: "user" as const, content: [{ type: "text" as const, text: "看一下" }] },
    {
      id: "assistant-run-1",
      role: "assistant" as const,
      content: [
        { type: "text" as const, text: "本季度处理量如下。" },
        { type: "tool-call" as const, toolCallId: "call-1", toolName: "harness_present_ui", args: presentUi(fallback) },
      ],
    },
  ];
  await act(async () => { thread!.import(ExportedMessageRepository.fromArray(messages)); });
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 100)); });
  return host;
}

afterEach(async () => {
  if (root) await act(async () => root!.unmount());
  host?.remove();
  root = undefined;
  host = undefined;
  resetRuntimeThreadScope();
});

it("drops Intelligent UI answer blocks on the assistant page", async () => {
  const node = await renderWithPresentation("assistant", "2026-11-17 技术雷达周报：19 条动态分三档。");
  expect(node.querySelector(".intelligent-ui-blocks")).toBeNull();
  expect(node.querySelector(".intelligent-ui")).toBeNull();
  expect(node.textContent).not.toContain("周报条目");
  expect(node.textContent).not.toContain("19 条动态分三档");
  expect(node.textContent).toContain("本季度处理量如下");
});

it("still renders Intelligent UI answer blocks on the task surface", async () => {
  const node = await renderWithPresentation("task", "2026-11-17 技术雷达周报：19 条动态分三档。");
  expect(node.querySelector(".intelligent-ui-blocks")).not.toBeNull();
  expect(node.textContent).toContain("周报条目");
});
