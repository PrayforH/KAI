// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
const mocks = vi.hoisted(() => ({ decide: vi.fn(), role: "owner" }));
vi.mock("../src/components/auth-provider", () => ({ useAuth: () => ({ membership: { role: mocks.role } }) }));
vi.mock("../src/lib/studio-client", () => ({ studioClient: { decideTryRunApproval: mocks.decide } }));
import { ApprovalBatch } from "../src/components/agent-studio/approval-batch";
import { RunTrace } from "../src/components/agent-studio/run-trace";
(globalThis as Record<string,unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let host: HTMLDivElement; let root: ReturnType<typeof createRoot>;
beforeEach(() => { mocks.role="owner"; mocks.decide.mockReset(); host=document.createElement("div"); document.body.append(host);root=createRoot(host); });
afterEach(() => { act(()=>root.unmount());host.remove();vi.unstubAllGlobals(); });
const approvals = ["read","write","expired"].map((id) => ({approval_id:id,tool_name:id,status:"pending" as const,argument_summary:{path:"/result.md"},risk:"medium",reason:"需要人工确认",expires_at:id==="expired"?"2000-01-01T00:00:00Z":"2099-01-01T00:00:00Z"}));
it("reports partial batch failures and retries only failed, selected approvals",async()=>{
 mocks.decide.mockResolvedValueOnce({}).mockRejectedValueOnce(new Error("版本已变化"));
 await act(async()=>root.render(<ApprovalBatch approvals={approvals}/>));
 const checks=host.querySelectorAll<HTMLInputElement>('input[type="checkbox"]');
 expect(checks[2].disabled).toBe(true);
 await act(async()=>{checks[0].click();checks[1].click();});
 await act(async()=>host.querySelector<HTMLButtonElement>("button")!.click());
 expect(mocks.decide.mock.calls).toEqual([["read","approved"],["write","approved"]]);
 expect(host.textContent).toContain("已批准本次调用");expect(host.textContent).toContain("版本已变化");
 mocks.decide.mockResolvedValueOnce({});
 await act(async()=>host.querySelector<HTMLButtonElement>("button")!.click());
 expect(mocks.decide.mock.calls[2]).toEqual(["write","approved"]);
 expect(host.querySelector<HTMLButtonElement>("button")!.disabled).toBe(true);
});
it("does not offer batch decisions to viewers",async()=>{
 mocks.role="viewer";await act(async()=>root.render(<ApprovalBatch approvals={approvals}/>));
 expect([...host.querySelectorAll<HTMLInputElement>("input")].every(input=>input.disabled)).toBe(true);
 expect(mocks.decide).not.toHaveBeenCalled();
});
it("filters Trace events while keeping the selected event payload visible",async()=>{
 const events=[{event_id:"e1",sequence:1,type:"model.route.selected",timestamp:"2026-09-20T00:00:00Z",payload:{model:"test-model"}},{event_id:"e2",sequence:2,type:"tool.request",timestamp:"2026-09-20T00:00:01Z",payload:{tool:"Read",path:"result.md"}}];
 await act(async()=>root.render(<RunTrace events={events}/>));
 await act(async()=>[...host.querySelectorAll<HTMLButtonElement>("button")].find(b=>b.textContent==="工具")!.click());
 expect(host.querySelectorAll("li")).toHaveLength(1);
 expect(host.querySelector("pre")?.textContent).toContain("result.md");
 expect(host.querySelector("ol")?.textContent).not.toContain("model.route.selected");
});
it("shows Trace access failure explicitly",async()=>{
 vi.stubGlobal("fetch",vi.fn(async()=>new Response("denied",{status:403})));
 await act(async()=>root.render(<RunTrace runId="private-run"/>));
 expect(host.querySelector('[role="alert"]')?.textContent).toContain("无权读取");
 expect(host.querySelectorAll("li")).toHaveLength(0);
});
