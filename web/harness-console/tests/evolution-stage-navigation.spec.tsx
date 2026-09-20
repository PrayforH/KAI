// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
const mocks=vi.hoisted(()=>({request:vi.fn()}));
vi.mock("../src/components/auth-provider",()=>({useAuth:()=>({membership:{role:"owner"}})}));
vi.mock("../src/lib/evolution-client",()=>({evolutionRequest:mocks.request}));
vi.mock("../src/components/agent-studio/candidate-comparison",()=>({CandidateComparison:()=> <div>逐用例对照</div>}));
vi.mock("../src/lib/studio-client",()=>({studioClient:{
 listAccessibleDrafts:async()=>[{name:"archive",draftId:"d"}],
 getDraft:async()=>({draftId:"d",revision:1,spec:{displayName:"档案助手",skills:[]}}),
 listEvalDatasets:async()=>[],
}}));
import { EvolutionWorkspace } from "../src/components/agent-studio/evolution-workspace";
(globalThis as Record<string,unknown>).IS_REACT_ACT_ENVIRONMENT=true;
let root:ReturnType<typeof createRoot>|undefined; let host:HTMLDivElement;
afterEach(()=>{if(root)act(()=>root?.unmount());host?.remove();vi.clearAllMocks();});
const job=(status:string)=>({jobId:"job",agentName:"archive",status:"active",revision:1,objective:"修正档案分类边界",baseline:{version:"1.0"},dataset:{version:1},budget:{maxCostUsd:1},allowedTargets:["systemPrompt"],expiresAt:"2099-01-01T00:00:00Z",experiences:[],observations:[],history:[],candidates:[{candidateId:"c",status,spec:{version:"2.0"},diff:"OLD to FIXED",trials:[],comparison:{status:"passed",improved:["one"],regressed:[],unresolved:[],unknownCostCount:0,reportHash:"hash"}}]});
async function render(stage:"experiments"|"review"|"release",status:string){
 mocks.request.mockResolvedValue([job(status)]);host=document.createElement("div");document.body.append(host);root=createRoot(host);
 await act(async()=>root!.render(<EvolutionWorkspace agentName="archive" stage={stage}/>));
 return [...host.querySelectorAll("button")].map(b=>b.textContent);
}
it("keeps candidate editing in experiments and review decisions out of that view",async()=>{
 const buttons=await render("experiments","review_pending");
 expect(buttons).toContain("编译并保存候选");expect(buttons).not.toContain("人工批准");
 expect(host.querySelector('a[href*="section=evaluation&job=job"]')).not.toBeNull();
});
it("lets reviewers inspect case evidence without editing the candidate",async()=>{
 const buttons=await render("review","review_pending");
 expect(buttons).toContain("人工批准");expect(buttons).not.toContain("编译并保存候选");expect(host.textContent).toContain("逐用例对照");
});
it("offers release only in the release stage and preserves review separation",async()=>{
 const buttons=await render("release","approved");expect(buttons).toContain("发布到个人版本");expect(buttons).not.toContain("人工批准");expect(buttons).not.toContain("编译并保存候选");
});
