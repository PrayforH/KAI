// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import {
  AssistantRuntimeProvider,
  ExportedMessageRepository,
  MessagePrimitive,
  ThreadPrimitive,
  useLocalRuntime,
  useThreadRuntime,
} from "@assistant-ui/react";
import { afterEach, expect, it, vi } from "vitest";
import { HarnessAssistantMessage } from "../src/components/agent-thread";
import {
  collapseRepeatedAssistantText,
  isDuplicateAssistantReply,
} from "../src/lib/message-text";
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

const answer = "转交结果：仍未启动，仍卡在缺少配置。\n\n这是第二次尝试，结果和第一次一致——不是偶发失败，而是接口确实没法传这两项。\n\n要哪个直接说，我照做。";
const repeatedOpening = answer.split("\n\n").slice(0, 2).join("\n\n");

it("collapses an accidentally repeated assistant block inside one reply", () => {
  expect(collapseRepeatedAssistantText(`${answer}\n\n${answer}`)).toBe(answer);
  expect(collapseRepeatedAssistantText(`${answer}\n\n${repeatedOpening}`)).toBe(answer);
});

it("keeps one copy of adjacent duplicate assistant turns", async () => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  await act(async () => root.render(<Fixture />));

  await act(async () => thread!.import(ExportedMessageRepository.fromArray([
    { id: "assistant-run-1", role: "assistant", content: [{ type: "text", text: answer }] },
    { id: "assistant-run-1-copy", role: "assistant", content: [{ type: "text", text: answer }] },
  ])));

  const messages = host.querySelectorAll(".harness-assistant-message");
  expect(messages).toHaveLength(1);
  expect(host.querySelector(".assistant-answer")?.textContent).toContain("转交结果");
});

it("does not merge a running reply with its previous turn", () => {
  expect(isDuplicateAssistantReply(answer, answer, "running")).toBe(false);
  expect(isDuplicateAssistantReply(answer, answer, "complete")).toBe(true);
  expect(isDuplicateAssistantReply(answer, `${answer}\n`, "complete")).toBe(true);
});
