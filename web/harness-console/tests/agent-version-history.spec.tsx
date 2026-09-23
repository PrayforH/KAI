// @vitest-environment jsdom
import React, { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { AgentVersionHistory } from "../src/components/agent-studio/agent-version-history";
import { DEFAULT_STUDIO_DRAFT } from "../src/lib/agent-studio";
import { studioClient, type DeepagentsProjectSource, type PersonalAgentVersion } from "../src/lib/studio-client";
vi.mock("../src/lib/studio-client", () => ({studioClient: {getPersonalAgentVersionFiles: vi.fn(), getDraftVersionFiles: vi.fn(), listDraftRevisions: vi.fn(), getDraftRevisionFiles: vi.fn()}}));
vi.mock("../src/components/agent-studio/project-source-diff", () => ({ProjectSourceDiff: ({change, theme}: any) => <div data-theme={theme}><del>{change.before?.content}</del><ins>{change.after?.content}</ins></div>}));
const source = (text: string): DeepagentsProjectSource => ({revision: 1, filename: "agent", digest: "", framework_version: "", files: [{path: "AGENTS.md", size: text.length, content: text, unavailable: null}]});
const versions: PersonalAgentVersion[] = ["0.2.0", "0.1.0"].map((version, index) => ({agent_id:"agent-a",name:"test",version,display_name:"测试",manifest_hash:"hash",package_hash:null,created_at:`2026-09-${22-index}T00:00:00Z`,current_version:"0.2.0"}));
const props = {draft:{...DEFAULT_STUDIO_DRAFT,id:"draft-a",agentId:"agent-a",revision:9,publishedVersion:"0.2.0"},changes:[],versions,loading:false,error:"",onRetry:vi.fn(),canPublish:true,promoting:"",confirmVersion:"",onConfirm:vi.fn(),onPromote:vi.fn()};
let root: Root; let host: HTMLDivElement;
beforeEach(() => {
  Object.assign(globalThis, {IS_REACT_ACT_ENVIRONMENT:true});
  document.documentElement.dataset.colorMode = "dark";
  vi.resetAllMocks();
  vi.mocked(studioClient.getPersonalAgentVersionFiles).mockImplementation(async (_id, version) => source(version === "0.2.0" ? "published new" : "published old"));
  vi.mocked(studioClient.getDraftVersionFiles).mockResolvedValue(source("saved draft"));
  vi.mocked(studioClient.listDraftRevisions).mockResolvedValue([{revision:9,updatedAt:"2026-09-23T00:00:00Z"}]);
  vi.mocked(studioClient.getDraftRevisionFiles).mockImplementation(async (_id, revision) => source(`draft revision ${revision}`));
  host=document.createElement("div");document.body.append(host);root=createRoot(host);
});
afterEach(async () => {await act(async () => root.unmount());host.remove();});
async function render() {await act(async () => root.render(<AgentVersionHistory {...props}/>));}
async function chooseVersion(version: string) {await act(async () => [...host.querySelectorAll<HTMLButtonElement>("nav button")].find(b => b.textContent?.startsWith(version))!.click());}
it("compares saved draft to publication without local change history", async () => {
  await render();
  expect(host.querySelector("del")?.textContent).toBe("published new");
  expect(host.querySelector("ins")?.textContent).toBe("saved draft");
  expect(host.querySelector('[data-theme="dark"]')).not.toBeNull();
  expect(host.textContent).toContain("修改 1");
  expect(studioClient.getDraftVersionFiles).toHaveBeenCalledWith("draft-a",9,expect.any(AbortSignal));
});
it("compares releases to predecessors and supports another baseline", async () => {
  await render();await chooseVersion("0.2.0");
  expect(host.querySelector("del")?.textContent).toBe("published old");
  expect(host.querySelector("ins")?.textContent).toBe("published new");
  const select=host.querySelector<HTMLSelectElement>('[aria-label="对比基准"]')!;
  await act(async () => {select.value="draft";select.dispatchEvent(new Event("change",{bubbles:true}));});
  expect(host.querySelector("del")?.textContent).toBe("saved draft");
  await chooseVersion("0.1.0");
  expect(host.textContent).toContain("新增 1");
  expect(host.querySelector("del")?.textContent).toBe("");
  expect(host.querySelector("ins")?.textContent).toBe("published old");
});
it("clears stale differences during loading and retries errors", async () => {
  await render();
  let reject!: (error: Error) => void;
  vi.mocked(studioClient.getPersonalAgentVersionFiles).mockImplementation(() => new Promise((_resolve, fail) => {reject=fail;}));
  await chooseVersion("0.1.0");
  expect(host.querySelector("ins")).toBeNull();
  expect(host.querySelector('[role="status"]')?.textContent).toContain("正在读取版本快照");
  await act(async () => reject(new Error("快照暂不可用")));
  expect(host.querySelector('[role="alert"]')?.textContent).toContain("快照暂不可用");
  vi.mocked(studioClient.getPersonalAgentVersionFiles).mockResolvedValue(source("recovered"));
  await act(async () => [...host.querySelectorAll("button")].find(b => b.textContent==="重试差异")!.click());
  expect(host.querySelector("ins")?.textContent).toBe("recovered");
});

it("reads persisted revisions, compares saved drafts and selects older revisions", async () => {
  vi.mocked(studioClient.listDraftRevisions).mockResolvedValue([9,8,7].map(revision => ({revision,updatedAt:"2026-09-23T00:00:00Z"})));
  await render();
  expect(host.querySelector("del")?.textContent).toBe("draft revision 8");
  expect(host.querySelector("ins")?.textContent).toBe("draft revision 9");
  await chooseVersion("草稿 r8");
  expect(host.querySelector("del")?.textContent).toBe("draft revision 7");
  expect(host.querySelector("ins")?.textContent).toBe("draft revision 8");
  expect(host.textContent).toContain("草稿记录从 r7 开始");
});
