// @vitest-environment jsdom
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { AssistantRuntimeProvider, useLocalRuntime } from "@assistant-ui/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { ComposerAddControl } from "../src/components/composer-add-control";
import { TaskKnowledgeProvider } from "../src/components/task-knowledge-context";
import type { StudioKnowledgeBase } from "../src/lib/studio-client";

const list = vi.hoisted(() => vi.fn());
vi.mock("../src/lib/studio-client", () => ({ studioClient: { listKnowledgeBases: list } }));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
vi.stubGlobal("ResizeObserver", class { observe() {} unobserve() {} disconnect() {} });
vi.stubGlobal("matchMedia", () => ({ matches: false, addEventListener() {}, removeEventListener() {} }));

const bases = [
  { reference: "cases", displayName: "案例库", engine: "weknora", kbType: "hybrid" },
  { reference: "policy", displayName: "政策库", engine: "weknora", kbType: "rag" },
] as StudioKnowledgeBase[];

let host: HTMLDivElement;
let root: Root;
let bound: string[][] = [];

function Harness({
  disabled = false,
  knowledgeAction,
  hideKnowledge = false,
}: {
  disabled?: boolean;
  knowledgeAction?: { label: string; onSelect: () => void };
  hideKnowledge?: boolean;
}) {
  const [selected, setSelected] = useState<string[]>([]);
  const [mode, setMode] = useState<"rag" | "wiki">("rag");
  const runtime = useLocalRuntime({ async *run() { yield { content: [{ type: "text" as const, text: "ok" }] }; } });
  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <TaskKnowledgeProvider
        selected={selected}
        onChange={(next) => { bound.push(next); setSelected(next); }}
        mode={mode}
        onModeChange={setMode}
      >
        <ComposerAddControl
          disabled={disabled}
          knowledgeAction={knowledgeAction}
          hideKnowledge={hideKnowledge}
        />
      </TaskKnowledgeProvider>
    </AssistantRuntimeProvider>
  );
}

beforeEach(() => {
  bound = [];
  list.mockReset().mockResolvedValue(bases);
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); });

const click = (selector: string) => act(() => host.querySelector<HTMLButtonElement>(selector)!.click());

it("keeps files and knowledge behind one icon", async () => {
  await act(async () => root.render(<Harness />));

  expect(host.querySelectorAll(".composer-add-trigger")).toHaveLength(1);
  // The old separate entries are gone: no lone attach button and no @ trigger.
  expect(host.querySelectorAll(".aui-composer-attach-icon")).toHaveLength(0);
  expect(host.querySelectorAll(".task-knowledge-trigger")).toHaveLength(0);

  click(".composer-add-trigger");
  const panel = host.querySelector('[role="dialog"][aria-label="添加文件或知识库"]');
  expect(panel).not.toBeNull();
  expect(panel!.textContent).toContain("文件");
  expect(panel!.textContent).toContain("添加文件");
  expect(panel!.textContent).toContain("知识库");
});

it("ticks a knowledge base in the side panel and binds it to the thread", async () => {
  await act(async () => root.render(<Harness />));
  click(".composer-add-trigger");
  await act(async () => { await Promise.resolve(); });

  click("li:nth-child(1) button");
  expect(bound).toEqual([["cases"]]);
  expect(host.querySelectorAll('.composer-add-panel [aria-pressed="true"]')).toHaveLength(1);

  click("li:nth-child(2) button");
  expect(bound).toEqual([["cases"], ["cases", "policy"]]);
  // The trigger reports the count, so the toolbar shows the binding without the panel.
  expect(host.querySelector(".composer-add-count")?.textContent).toBe("2");

  click(".task-knowledge-clear");
  expect(bound.at(-1)).toEqual([]);
});

it("closes on Escape and returns focus to the icon", async () => {
  await act(async () => root.render(<Harness />));
  click(".composer-add-trigger");
  act(() => document.activeElement?.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
  expect(host.querySelector('[role="dialog"]')).toBeNull();
  expect(document.activeElement).toBe(host.querySelector(".composer-add-trigger"));
});

it("stays closed while the composer is locked", async () => {
  await act(async () => root.render(<Harness disabled />));
  const trigger = host.querySelector<HTMLButtonElement>(".composer-add-trigger")!;
  expect(trigger.disabled).toBe(true);
  act(() => trigger.click());
  expect(host.querySelector('[role="dialog"]')).toBeNull();
});

it("offers the agent's own knowledge configuration in scope mode", async () => {
  const configure = vi.fn();
  await act(async () => root.render(
    <Harness knowledgeAction={{ label: "配置智能体知识库", onSelect: configure }} />,
  ));

  click(".composer-add-trigger");
  const panel = host.querySelector(".composer-add-panel")!;
  expect(panel.textContent).toContain("配置智能体知识库");
  // Scope mode binds knowledge at the agent level, so there is nothing to tick here
  // and nothing to count on the icon.
  expect(host.querySelectorAll(".task-knowledge-option")).toHaveLength(0);
  expect(host.querySelector(".composer-add-count")).toBeNull();

  click(".composer-add-panel .composer-add-action");
  expect(configure).toHaveBeenCalledTimes(1);
  expect(host.querySelector(".composer-add-panel")).toBeNull();
});

it("keeps files but drops knowledge when the composer is compact", async () => {
  await act(async () => root.render(<Harness hideKnowledge />));

  click(".composer-add-trigger");
  const panel = host.querySelector(".composer-add-panel")!;
  expect(panel.textContent).toContain("添加文件");
  expect(panel.textContent).not.toContain("知识库");
});
