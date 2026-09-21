// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { agentSection, nextAgentAction, qualityGroups } from "../src/lib/agent-workspace";
import type { EvolutionJob } from "../src/lib/evolution-client";
import type { StudioQualityScore } from "../src/lib/studio-client";
const mock = vi.hoisted(() => ({ summaries: vi.fn(), quality: vi.fn(), datasets: vi.fn(), versions: vi.fn(), jobs: vi.fn(), history: vi.fn() }));
vi.mock("../src/components/auth-provider", () => ({ useAuth: () => ({user:{user_id:"owner"},membership:{role:"owner"}}) }));
vi.mock("../src/lib/studio-client", () => ({ studioClient: {listAccessibleDrafts:mock.summaries,listQualityScores:mock.quality,listEvalDatasets:mock.datasets,listPersonalAgentVersions:mock.versions,listTryRuns:mock.history} }));
vi.mock("../src/lib/evolution-client", () => ({ evolutionRequest:mock.jobs }));
vi.mock("../src/components/agent-studio/evolution-workspace", () => ({EvolutionWorkspace:()=> <div>实验组件</div>}));
vi.mock("../src/components/agent-studio/agent-operations-workspace", () => ({AgentOperationsWorkspace:()=> <div>部署组件</div>}));
import { AgentWorkspace } from "../src/components/agent-studio/agent-workspace";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let host: HTMLDivElement; let root: ReturnType<typeof createRoot>;
beforeEach(() => {
  vi.clearAllMocks(); host=document.createElement("div");document.body.append(host);root=createRoot(host);
  mock.summaries.mockResolvedValue([{draftId:"draft",agentId:"id",name:"archive",displayName:"档案助手",spaceId:null,version:"1.0",publishedVersion:"1.0"}]);
  mock.history.mockResolvedValue([]);
  mock.quality.mockResolvedValue([]);mock.datasets.mockResolvedValue([]);mock.jobs.mockResolvedValue([]);
  mock.versions.mockResolvedValue([{version:"2.0",current_version:"2.0",created_at:"2026-09-20T00:00:00Z"}]);
});
afterEach(() => { act(()=>root.unmount());host.remove(); });
it("uses the authoritative current version, preserves construction, and has real stage navigation", async () => {
  await act(async()=>root.render(<AgentWorkspace agentName="archive" section="overview"/>));
  const play=[...host.querySelectorAll("a")].find(a=>a.textContent?.includes("开始使用"));
  expect(play?.getAttribute("href")).toBe("/?agent=archive&version=2.0&owner=owner");
  expect(host.querySelector('a[aria-current="page"]')?.textContent).toContain("概览");
  expect(host.textContent).toContain("构建版本 1.0");
  expect(host.querySelector('a[href="/studio/agents/archive?section=playground&draft=draft"]')).not.toBeNull();
});
it("does not read personal quality or experiments for an identically named team agent", async () => {
  mock.summaries.mockResolvedValue([{draftId:"team-draft",spaceId:"team",name:"archive",displayName:"团队档案"}]);
  await act(async()=>root.render(<AgentWorkspace agentName="archive" draftId="team-draft" section="diagnostics"/>));
  expect(mock.quality).not.toHaveBeenCalled();expect(mock.jobs).not.toHaveBeenCalled();
  expect(host.textContent).toContain("团队智能体沿用团队管理权限");
});
it("reports failed evidence reads instead of representing them as zero activity", async () => {
  mock.quality.mockRejectedValue(new Error("offline"));
  await act(async()=>root.render(<AgentWorkspace agentName="archive" section="overview"/>));
  expect(host.querySelector('[role="alert"]')?.textContent).toContain("运行质量暂时无法读取");
  expect(host.textContent).toContain("不可用");
});
it("keeps completed approvals out of the action inbox", () => {
  const jobs=[{status:"completed",candidates:[{status:"approved"}]},{status:"active",candidates:[{status:"review_pending"}]}] as EvolutionJob[];
  expect(nextAgentAction(jobs).find(a=>a.section==="evaluation")?.count).toBe(1);
  expect(nextAgentAction(jobs).find(a=>a.label==="候选等待发布")?.count).toBe(0);
  expect(agentSection("invalid")).toBe("overview");
});
it("excludes experiment records while preserving unknown production evidence", () => {
  const values=[{runId:"live",agentVersion:"1.0",evalRunId:null,value:null,createdAt:"2026-09-20"},{runId:"eval",agentVersion:"1.0",evalRunId:"eval-1",createdAt:"2026-09-20"},{runId:"preview",agentVersion:"evo-preview-x",evalRunId:null,createdAt:"2026-09-20"}] as StudioQualityScore[];
  expect(qualityGroups(values).map(([id])=>id)).toEqual(["live"]);
  expect(qualityGroups(values)[0][1][0].value).toBeNull();
});
