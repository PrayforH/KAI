// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, expect, it, vi } from "vitest";
import { ApprovalCard, formatApprovalReason } from "../src/components/approval-card";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT=true;
let host: HTMLDivElement;let root:Root;
const details={approval_id:"approval-a",run_id:"run-a",tool_name:"Bash",argument_summary:{command:"python outputs/report.py",Authorization:"hidden-credential"},reason:"matched policy rule bash-review",risk:"high"};
async function mount(onDecision=vi.fn().mockResolvedValue(undefined),extra={}){
 host=document.createElement("div");document.body.append(host);root=createRoot(host);
 await act(async()=>root.render(<ApprovalCard details={{...details,...extra}} complete={false} onDecision={onDecision}/>));return onDecision;
}
afterEach(async()=>{if(root)await act(async()=>root.unmount());host?.remove();});
it("shows the command before approval and authorizes exactly one call despite duplicate clicks",async()=>{
 let finish!:()=>void;const callback=vi.fn(()=>new Promise<void>(resolve=>finish=resolve));await mount(callback);
 expect(host.textContent).toContain("python outputs/report.py");expect(host.textContent).toContain("仅对本次调用生效");expect(host.textContent).not.toContain("hidden-credential");expect(host.textContent).not.toContain("始终允许");
 const allow=host.querySelectorAll("button")[1];
 await act(async()=>{allow.click();allow.click();});expect(callback.mock.calls).toEqual([["approved"]]);expect(allow.disabled).toBe(true);
 await act(async()=>finish());expect(host.textContent).toContain("已允许本次操作");expect(host.querySelector("button")).toBeNull();
});
it("preserves typing focus and permits retry after a failed decision request",async()=>{
 const input=document.createElement("textarea");document.body.append(input);input.focus();
 const callback=vi.fn().mockRejectedValueOnce(new Error("网络暂时不可用")).mockResolvedValue(undefined);
 try{await mount(callback);expect(document.activeElement).toBe(input);await act(async()=>host.querySelectorAll("button")[1].click());expect(host.querySelector('[role="alert"]')?.textContent).toContain("网络暂时不可用");await act(async()=>host.querySelectorAll("button")[1].click());expect(callback).toHaveBeenCalledTimes(2);}finally{input.remove();}
});
it("prevents expired requests and explicit rejection never sends approval",async()=>{
 const callback=await mount(undefined,{expires_at:"2020-01-01T00:00:00Z"});expect(host.textContent).toContain("审批已过期");expect(host.querySelector("button")).toBeNull();expect(callback).not.toHaveBeenCalled();
 await act(async()=>root.render(<ApprovalCard details={{...details,approval_id:"approval-b"}} complete={false} onDecision={callback}/>));await act(async()=>host.querySelector("button")!.click());expect(callback.mock.calls).toEqual([["rejected"]]);expect(host.textContent).toContain("已拒绝本次操作");
});
it("keeps an earlier async decision from settling the next approval",async()=>{
 let finish!:()=>void;const callback=vi.fn(()=>new Promise<void>(resolve=>finish=resolve));await mount(callback);
 await act(async()=>host.querySelectorAll("button")[1].click());
 await act(async()=>root.render(<ApprovalCard details={{...details,approval_id:"approval-b"}} complete={false} onDecision={vi.fn().mockResolvedValue(undefined)}/>));
 await act(async()=>finish());expect(host.textContent).toContain("允许一次");expect(host.textContent).not.toContain("已允许本次操作");
 expect(formatApprovalReason("matched policy rule sensitive-memory-review")).toContain("敏感内容");
});
