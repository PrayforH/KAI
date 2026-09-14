// @vitest-environment jsdom
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { renderToStaticMarkup } from "react-dom/server";
import ReactMarkdown from "react-markdown";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { TaskKnowledgeProvider, TaskKnowledgeControl, TaskKnowledgeModeSwitch, TaskKnowledgeSelection } from "../src/components/task-knowledge-context";
import { citationsForTurn, citationTarget, dedupeCitations, knowledgeUrlTransform, parseWikiTarget, remarkWikiLinks } from "../src/lib/knowledge-links";
import type { StudioKnowledgeBase } from "../src/lib/studio-client";

const list = vi.hoisted(() => vi.fn());
vi.mock("../src/lib/studio-client", () => ({ studioClient: { listKnowledgeBases: list } }));
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let host: HTMLDivElement;
let root: Root;
const bases = [
  { reference: "cases", displayName: "案例库", engine: "weknora", kbType: "hybrid" },
  { reference: "policy", displayName: "政策库", engine: "weknora", kbType: "rag" },
] as StudioKnowledgeBase[];
function Harness() {
  const [selected, setSelected] = useState<string[]>([]);
  const [mode, setMode] = useState<"rag" | "wiki">("rag");
  return <TaskKnowledgeProvider selected={selected} onChange={setSelected} mode={mode} onModeChange={setMode}>
    <TaskKnowledgeControl disabled={false} /><TaskKnowledgeModeSwitch disabled={false} />
    <output>{selected.join(",")}</output>
  </TaskKnowledgeProvider>;
}
beforeEach(() => { list.mockReset().mockResolvedValue(bases); host = document.createElement("div"); document.body.append(host); root = createRoot(host); });
afterEach(() => { act(() => root.unmount()); host.remove(); });
const click = (selector: string) => act(() => host.querySelector<HTMLButtonElement>(selector)!.click());
it("opens a real picker, preserves multi-selection, and Escape returns focus", async () => {
  await act(async () => root.render(<Harness />));
  click(".task-knowledge-trigger");
  expect(host.querySelector('[role="dialog"]')).not.toBeNull();
  click("li:nth-child(1) button"); click("li:nth-child(2) button");
  expect(host.querySelector("output")?.textContent).toBe("cases,policy");
  expect(host.querySelectorAll('[aria-pressed="true"]')).toHaveLength(2);
  act(() => document.activeElement?.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
  expect(host.querySelector('[role="dialog"]')).toBeNull();
  expect(document.activeElement).toBe(host.querySelector(".task-knowledge-trigger"));
  click(".task-knowledge-trigger"); click(".task-knowledge-clear");
  expect(host.querySelector("output")?.textContent).toBe("");
});
it("disables Wiki for a RAG-only selection, and allows turning it off after selection changes", async () => {
  await act(async () => root.render(<Harness />));
  click(".task-knowledge-trigger"); click("li:nth-child(2) button");
  expect(host.querySelector<HTMLButtonElement>('[role="switch"]')!.disabled).toBe(true);
  click("li:nth-child(1) button"); click('[role="switch"]'); click("li:nth-child(1) button");
  expect(host.querySelector<HTMLButtonElement>('[role="switch"]')!.disabled).toBe(false);
  click('[role="switch"]');
  expect(host.querySelector('[role="switch"]')?.getAttribute("aria-checked")).toBe("false");
});
it("shows retry when loading fails instead of reporting an empty catalog", async () => {
  list.mockRejectedValueOnce(new Error("offline"));
  await act(async () => root.render(<Harness />)); click(".task-knowledge-trigger");
  expect(host.querySelector('[role="alert"]')?.textContent).toContain("加载失败");
  await act(async () => host.querySelector<HTMLButtonElement>('[role="alert"] button')!.click());
  expect(host.querySelectorAll("li")).toHaveLength(2);
});
it("qualifies Wiki links and leaves inline/fenced code literal", () => {
  const rendered = renderToStaticMarkup(<ReactMarkdown remarkPlugins={[remarkWikiLinks]} urlTransform={knowledgeUrlTransform}>{'[[cases::entity/a|甲]] `[[demo|例]]`\n\n```text\n[[sample|例子]]\n```'}</ReactMarkdown>);
  expect(rendered).toContain('href="wiki:cases%3A%3Aentity%2Fa"');
  expect(rendered).toContain('<code>[[demo|例]]</code>');
  expect(rendered).not.toContain('wiki:sample');
  expect(parseWikiTarget("cases::entity/a")).toEqual({ reference: "cases", slug: "entity/a" });
  expect(parseWikiTarget("entity/a")).toEqual({ slug: "entity/a" });
  expect(knowledgeUrlTransform("javascript:alert(1)")).toBe("");
});
it("deduplicates by source and chunk, retaining same-named chunks in different bases", () => {
  const a = { sourceReference: "cases", chunkId: "same", index: 1, content: "a" };
  const result = dedupeCitations([a, a, { ...a, sourceReference: "policy" }]);
  expect(result.map((item) => item.index)).toEqual([1, 2]);
  expect(citationTarget(result[0])).not.toBe(citationTarget(result[1]));
});

it("never attaches a new run's sources to an older canonical answer", () => {
  const citation = { sourceReference: "cases", chunkId: "one", index: 1, content: "evidence" };
  const observed = { runId: "new", tools: [{ citations: [citation] }] } as unknown as import("../src/lib/run-view-model").RunViewModel;
  expect(citationsForTurn("assistant-old", true, observed)).toBeUndefined();
  expect(citationsForTurn("assistant-new", false, observed)).toEqual([citation]);
  const durable = { run_id: "old", status: "succeeded", items: [], updated_at: "2026-09-09T00:00:00Z" } as unknown as import("../src/lib/activity-schema").RunActivity;
  expect(citationsForTurn("assistant-old", false, observed, durable)).toEqual([]);
});

it("opens the shared multi-picker from the context shelf and removes a selected base", async () => {
  function Shelf() {
    const [selected, setSelected] = useState(["cases", "policy"]);
    return <TaskKnowledgeProvider selected={selected} onChange={setSelected} mode="rag" onModeChange={() => {}}>
      <TaskKnowledgeSelection disabled={false} /><TaskKnowledgeControl disabled={false} />
    </TaskKnowledgeProvider>;
  }
  await act(async () => root.render(<Shelf />));
  click('[aria-label="调整知识库 案例库"]');
  expect(host.querySelectorAll('[role="dialog"]')).toHaveLength(1);
  expect(host.querySelectorAll('[aria-pressed="true"]')).toHaveLength(2);
  click('[aria-label="移除知识库 案例库"]');
  expect(host.querySelector('[aria-label="调整知识库 案例库"]')).toBeNull();
  expect(host.querySelector('[aria-label="选择知识库，已选 1 个"]')).not.toBeNull();
});
