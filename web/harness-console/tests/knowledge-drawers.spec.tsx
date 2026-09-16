// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { KnowledgeCitations } from "../src/components/knowledge/knowledge-citations";
import { AnswerCitationProvider, useAnswerCitations } from "../src/components/knowledge/answer-citation-context";
import { WikiPageDrawer } from "../src/components/knowledge/wiki-page-drawer";
import { KnowledgeDrawerLayer } from "../src/components/knowledge/knowledge-drawer-layer";
const api = vi.hoisted(() => ({ getKnowledgeDocument: vi.fn(), listKnowledgeDocumentChunks: vi.fn(), getKnowledgeDocumentChunk: vi.fn(), getWikiPage: vi.fn(), resolveWikiPage: vi.fn(), listKnowledgeBases: vi.fn() }));
vi.mock("../src/lib/studio-client", () => ({ studioClient: api }));
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let host: HTMLDivElement; let root: Root;
beforeEach(() => {
  vi.resetAllMocks();
  api.getKnowledgeDocument.mockResolvedValue(null);
  api.listKnowledgeDocumentChunks.mockResolvedValue([]);
  api.getKnowledgeDocumentChunk.mockResolvedValue(null);
  Element.prototype.scrollIntoView = vi.fn();
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); });
function deferred<T>() { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; }
const page = (title: string, content = title) => ({ title, content, slug: title, pageType: "entity", categoryPath: [], summary: "" });
const chunk = (chunkId: string, seq: number, content: string) => ({ tenantId: "t", sourceReference: "cases", documentId: `doc-${chunkId}`, chunkId, title: chunkId, content, seq });
it("does not overwrite a newly opened citation with an older request", async () => {
  const first = deferred<unknown>(); const second = deferred<unknown>();
  api.listKnowledgeDocumentChunks.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
  const citations = ["a", "b"].map((id, index) => ({ index: index + 1, sourceReference: "cases", chunkId: id, documentId: `doc-${id}`, title: id, content: `excerpt-${id}` }));
  await act(async () => root.render(<KnowledgeCitations citations={citations} />));
  const buttons = host.querySelectorAll<HTMLButtonElement>("button");
  act(() => buttons[0].click()); act(() => buttons[1].click());
  await act(async () => second.resolve([chunk("b", 1, "第二份内容")]));
  await act(async () => first.resolve([chunk("a", 1, "过期内容")]));
  expect(document.querySelector('[role="dialog"]')?.textContent).toContain("第二份内容");
  expect(document.body.textContent).not.toContain("过期内容");
});
it("opens a citation as its document with the cited slice marked, and drills into the chunks", async () => {
  const citation = { index: 1, sourceReference: "cases", chunkId: "c2", documentId: "doc-c2", title: "起诉书", content: "命中摘录" };
  api.listKnowledgeDocumentChunks.mockResolvedValue([
    chunk("c1", 1, "第一段"),
    chunk("c2", 2, "第二段"),
    chunk("c3", 3, "第三段"),
  ]);
  api.getKnowledgeDocument.mockResolvedValue({ title: "起诉书（徐某）.pdf", fileType: "pdf", parseStatus: "completed", createdAt: "", description: "文档摘要" });
  await act(async () => root.render(<KnowledgeCitations citations={[citation]} />));
  await act(async () => host.querySelector<HTMLButtonElement>("button")!.click());
  const dialog = () => document.querySelector('[role="dialog"]')!;
  expect(api.listKnowledgeDocumentChunks).toHaveBeenCalledWith("cases", "doc-c2");
  expect(dialog().getAttribute("aria-label")).toBe("引用文档详情");
  expect(dialog().textContent).toContain("起诉书（徐某）.pdf");
  expect(dialog().textContent).toContain("共 3 个片段");
  expect(dialog().textContent).toContain("第一段");
  expect(dialog().textContent).toContain("第三段");
  expect(dialog().textContent).toContain("本次引用 · 片段 2");
  expect(dialog().textContent).toContain("文档摘要");
  const toggles = Array.from(dialog().querySelectorAll("button"));
  await act(async () => toggles.find((node) => node.textContent === "查看分块")!.click());
  expect(dialog().textContent).toContain("片段 1");
  expect(dialog().textContent).toContain("片段 3");
  expect(dialog().querySelectorAll("article")).toHaveLength(3);
  await act(async () => toggles.find((node) => node.textContent === "全文")!.click());
  expect(dialog().querySelectorAll("article")).toHaveLength(0);
});

it("falls back to the answer excerpt when a citation has no engine document", async () => {
  const citation = { index: 1, sourceReference: "snapshot", chunkId: "old-1", title: "旧切片", content: "快照正文" };
  await act(async () => root.render(<KnowledgeCitations citations={[citation]} />));
  await act(async () => host.querySelector<HTMLButtonElement>("button")!.click());
  expect(api.listKnowledgeDocumentChunks).not.toHaveBeenCalled();
  expect(document.querySelector('[role="dialog"]')?.textContent).toContain("快照正文");
});

it("keeps the answer excerpt visible when the cited slice is gone after a re-parse", async () => {
  const citation = { index: 2, sourceReference: "cases", chunkId: "old-slice", documentId: "doc-new", title: "起诉书", content: "旧解析的命中摘录" };
  api.listKnowledgeDocumentChunks.mockResolvedValue([chunk("c1", 1, "重新解析后的正文")]);
  api.getKnowledgeDocument.mockResolvedValue({ title: "起诉书.pdf", fileType: "pdf", parseStatus: "completed", createdAt: "", description: "" });
  await act(async () => root.render(<KnowledgeCitations citations={[citation]} />));
  await act(async () => host.querySelector<HTMLButtonElement>("button")!.click());
  const text = document.querySelector('[role="dialog"]')!.textContent ?? "";
  expect(text).toContain("重新解析后的正文");
  expect(text).toContain("旧解析的命中摘录");
  expect(text).toContain("未匹配到该切片");
});

it("opens an explicit Wiki base without searching unrelated bases, and clears stale pages on failure", async () => {
  api.getWikiPage.mockResolvedValueOnce(page("甲", "| 字段 | 值 |\n| --- | --- |\n| 名称 | 甲 |"));
  await act(async () => root.render(<WikiPageDrawer reference="cases" slug="entity/a" onClose={() => {}} />));
  expect(api.getWikiPage).toHaveBeenCalledWith("cases", "entity/a");
  expect(api.listKnowledgeBases).not.toHaveBeenCalled();
  expect(document.querySelector("table")).not.toBeNull();
  api.getWikiPage.mockRejectedValueOnce(new Error("页面不存在"));
  await act(async () => root.render(<WikiPageDrawer reference="policy" slug="entity/b" onClose={() => {}} />));
  expect(document.body.textContent).toContain("页面不存在");
  expect(document.querySelector("table")).toBeNull();
});
it("refuses to guess the source for ambiguous legacy Wiki links", async () => {
  api.resolveWikiPage.mockRejectedValue(new Error("多个知识库存在同名页面，无法确定来源"));
  await act(async () => root.render(<WikiPageDrawer slug="entity/a" onClose={() => {}} />));
  expect(document.body.textContent).toContain("无法确定来源");
});

it("portals Wiki above the shell and restores navigation focus after Escape", async () => {
  api.getWikiPage.mockResolvedValue(page("实体"));
  const opener = document.createElement("button"); host.append(opener); opener.focus();
  const close = vi.fn();
  await act(async () => root.render(<WikiPageDrawer reference="cases" slug="entity/a" onClose={close} />));
  const dialog = document.querySelector('[role="dialog"]')!;
  expect(host.contains(dialog)).toBe(false);
  expect(host.inert).toBe(true);
  expect(dialog.contains(document.activeElement)).toBe(true);
  act(() => document.activeElement?.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
  expect(close).toHaveBeenCalledOnce();
  act(() => root.render(null));
  expect(host.inert).not.toBe(true);
  expect(document.body.style.overflow).not.toBe("hidden");
});

it("opens only one drawer for an inline citation even when activity also lists the source", async () => {
  const citation = { index: 1, sourceReference: "cases", chunkId: "a", title: "依据", content: "原文" };
  api.getKnowledgeDocumentChunk.mockResolvedValue({ content: "原文", documentId: "a", seq: 1 });
  function Inline() {
    const answer = useAnswerCitations();
    return <button onClick={() => answer?.request(citation)}>段落来源</button>;
  }
  await act(async () => root.render(<AnswerCitationProvider citations={[citation]}>
    <KnowledgeCitations citations={[citation]} /><Inline /><KnowledgeCitations citations={[citation]} showSources={false} />
  </AnswerCitationProvider>));
  await act(async () => Array.from(host.querySelectorAll("button")).find((node) => node.textContent === "段落来源")!.click());
  expect(document.querySelectorAll('[role="dialog"]')).toHaveLength(1);
  expect(api.getKnowledgeDocumentChunk).toHaveBeenCalledOnce();
});

it("keeps form fields in the modal focus cycle, including a final textarea", async () => {
  await act(async () => root.render(<KnowledgeDrawerLayer onClose={() => {}}>
    <div role="dialog"><button>关闭</button><input type="hidden" /><select aria-label="角色"><option>查看</option></select><textarea aria-label="说明" /></div>
  </KnowledgeDrawerLayer>));
  const dialog = document.querySelector('[role="dialog"]')!;
  const first = dialog.querySelector("button")!;
  const last = dialog.querySelector("textarea")!;
  last.focus();
  act(() => last.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true, cancelable: true })));
  expect(document.activeElement).toBe(first);
  act(() => first.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true, cancelable: true })));
  expect(document.activeElement).toBe(last);
});
