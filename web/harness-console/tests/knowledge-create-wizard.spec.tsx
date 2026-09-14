// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { KnowledgeConsole } from "../src/components/knowledge/knowledge-console";

const api = vi.hoisted(() => ({ listKnowledgeBases: vi.fn(), createKnowledgeBase: vi.fn() }));
vi.mock("../src/lib/studio-client", () => ({ studioClient: api }));
vi.mock("../src/components/auth-provider", () => ({
  useAuth: () => ({ user: { user_id: "user-1" }, membership: { role: "owner" } }),
}));
vi.mock("next/link", () => ({
  default: ({ children }: { children?: unknown }) => <span>{children as never}</span>,
}));
vi.mock("../src/components/knowledge/knowledge-members-panel", () => ({
  KnowledgeMembersPanel: () => null,
}));

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

let host: HTMLDivElement;
let root: Root;

beforeEach(() => {
  vi.resetAllMocks();
  api.listKnowledgeBases.mockResolvedValue([]);
  api.createKnowledgeBase.mockResolvedValue({ reference: "case-library", displayName: "案例库" });
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

function fill(element: HTMLInputElement | HTMLTextAreaElement, value: string) {
  const prototype = element instanceof HTMLTextAreaElement
    ? HTMLTextAreaElement.prototype
    : HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(prototype, "value")!.set!.call(element, value);
  element.dispatchEvent(new Event("input", { bubbles: true }));
}

function button(label: string): HTMLButtonElement {
  const match = Array.from(document.querySelectorAll("button")).find(
    (node) => (node.textContent ?? "").trim() === label,
  );
  if (!match) throw new Error(`button not found: ${label}`);
  return match;
}

/** Type cards carry both a name and a hint paragraph. */
function typeCard(label: string): HTMLButtonElement {
  const match = Array.from(document.querySelectorAll("button")).find((node) => {
    const heading = node.querySelector("p");
    return (heading?.textContent ?? "").trim() === label;
  });
  if (!match) throw new Error(`type card not found: ${label}`);
  return match;
}

async function openWizard(kbType: "RAG" | "Wiki" | "混合") {
  await act(async () => root.render(<KnowledgeConsole />));
  act(() => button("新建知识库").click());
  act(() => typeCard(kbType).click());
  const dialog = document.querySelector('[role="dialog"]')!;
  const inputs = dialog.querySelectorAll("input");
  act(() => fill(inputs[0], "case-library"));
  act(() => fill(inputs[1], "案例库"));
  return dialog;
}

async function submit() {
  await act(async () => button("创建").click());
  expect(api.createKnowledgeBase).toHaveBeenCalledOnce();
  return api.createKnowledgeBase.mock.calls[0][0];
}

it("sends chunking and Wiki synthesis options for a hybrid base", async () => {
  const dialog = await openWizard("混合");
  expect(dialog.textContent).toContain("分块设置");
  expect(dialog.textContent).toContain("Wiki 设置");
  expect(dialog.textContent).toContain("提取粒度");
  expect(dialog.textContent).toContain("Wiki 内容生成要求");
  expect(dialog.textContent).toContain("Wiki 提取重点");
  expect(dialog.textContent).toContain("单次最大页面数");

  act(() => fill(dialog.querySelector("#kb-chunk-size")!, "3200"));
  act(() => fill(dialog.querySelector("#kb-chunk-overlap")!, "200"));
  act(() => button("详尽").click());
  act(() => fill(dialog.querySelector("#kb-wiki-content")!, "  用法务口吻  "));
  act(() => fill(dialog.querySelector("#kb-wiki-extraction")!, "重点识别责任主体"));
  act(() => fill(dialog.querySelector("#kb-wiki-pages")!, "24"));

  const body = await submit();
  expect(body.kbType).toBe("hybrid");
  expect(body.config).toEqual({
    chunkSize: 3200,
    chunkOverlap: 200,
    wikiGranularity: "exhaustive",
    wikiContentInstructions: "用法务口吻",
    wikiExtractionInstructions: "重点识别责任主体",
    wikiMaxPagesPerIngest: 24,
  });
});

it("offers only the RAG group for a RAG base and leaves chunking at the platform default", async () => {
  const dialog = await openWizard("RAG");
  expect(dialog.textContent).toContain("分块设置");
  expect(dialog.textContent).not.toContain("Wiki 设置");
  expect(dialog.querySelector("#kb-wiki-content")).toBeNull();

  const body = await submit();
  // A blank field must not pin a value: the engine keeps its own default.
  expect(body.config).toEqual({});
});

it("offers only the Wiki group for a Wiki base and defaults to standard granularity", async () => {
  const dialog = await openWizard("Wiki");
  expect(dialog.textContent).not.toContain("分块设置");
  expect(dialog.textContent).toContain("Wiki 设置");
  expect(dialog.querySelector("#kb-chunk-size")).toBeNull();

  const body = await submit();
  expect(body.config).toEqual({ wikiGranularity: "standard" });
});
