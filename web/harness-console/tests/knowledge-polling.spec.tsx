// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { KnowledgeBaseDetail } from "../src/components/knowledge/knowledge-base-detail";
const api = vi.hoisted(() => ({ getKnowledgeBase: vi.fn(), listKnowledgeDocuments: vi.fn(), deleteKnowledgeDocument: vi.fn() }));
vi.mock("../src/lib/studio-client", () => ({ studioClient: api }));
vi.mock("../src/components/knowledge/knowledge-wiki-panel", () => ({ KnowledgeWikiPanel: () => null }));
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let root: Root; let host: HTMLDivElement;
const doc = { documentId:"d1", title:"报告", parseStatus:"processing", summaryStatus:"pending", fileType:"pdf", createdAt:"", updatedAt:"" };
beforeEach(() => {
  vi.useFakeTimers(); vi.resetAllMocks();
  Object.defineProperty(document,"visibilityState",{configurable:true,value:"visible"});
  api.getKnowledgeBase.mockResolvedValue({displayName:"资料", engine:"weknora", reference:"test"});
  api.listKnowledgeDocuments.mockResolvedValue([doc]);
  host=document.createElement("div");document.body.append(host);root=createRoot(host);
});
afterEach(() => { act(() => root.unmount()); host.remove(); vi.useRealTimers(); });
it("polls initial processing documents, stops at completion, and leaves no timer on unmount", async () => {
  await act(async () => root.render(<KnowledgeBaseDetail reference="test" />));
  expect(api.listKnowledgeDocuments).toHaveBeenCalledTimes(1);
  api.listKnowledgeDocuments.mockResolvedValue([{...doc,parseStatus:"completed"}]);
  await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
  expect(api.listKnowledgeDocuments).toHaveBeenCalledTimes(2);
  await act(async () => { await vi.advanceTimersByTimeAsync(60_000); });
  expect(api.listKnowledgeDocuments).toHaveBeenCalledTimes(2);
});
it("pauses in the background and does not overlap slow requests", async () => {
  await act(async () => root.render(<KnowledgeBaseDetail reference="test" />));
  Object.defineProperty(document,"visibilityState",{configurable:true,value:"hidden"});
  await act(async () => { document.dispatchEvent(new Event("visibilitychange")); await vi.advanceTimersByTimeAsync(60_000); });
  expect(api.listKnowledgeDocuments).toHaveBeenCalledTimes(1);
  let finish!: (docs: unknown[]) => void;
  api.listKnowledgeDocuments.mockImplementation(() => new Promise(resolve => { finish=resolve; }));
  Object.defineProperty(document,"visibilityState",{configurable:true,value:"visible"});
  await act(async () => { document.dispatchEvent(new Event("visibilitychange")); await vi.advanceTimersByTimeAsync(30_000); });
  expect(api.listKnowledgeDocuments).toHaveBeenCalledTimes(2);
  await act(async () => { finish([{...doc,parseStatus:"completed"}]); });
});
it("ignores a previous knowledge base response after navigation", async () => {
  let finish!: (docs: unknown[]) => void;
  api.listKnowledgeDocuments.mockImplementationOnce(() => new Promise(resolve => { finish=resolve; }));
  await act(async () => root.render(<KnowledgeBaseDetail reference="old" />));
  api.listKnowledgeDocuments.mockResolvedValue([{...doc,title:"新文档",parseStatus:"completed"}]);
  await act(async () => root.render(<KnowledgeBaseDetail reference="new" />));
  await act(async () => { finish([doc]); });
  expect(host.textContent).toContain("新文档"); expect(host.textContent).not.toContain("报告");
});
