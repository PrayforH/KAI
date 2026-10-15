// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect,it,vi } from "vitest";
import { ActivitySummary } from "../src/components/activity-summary";
import { runActivitySchema } from "../src/lib/activity-schema";
(globalThis as Record<string,unknown>).IS_REACT_ACT_ENVIRONMENT=true;
it("keeps decoded tool output in its original inline disclosure without a dialog or table conversion",async()=>{
 vi.stubGlobal("ResizeObserver",class{observe(){}disconnect(){}});vi.stubGlobal("requestAnimationFrame",()=>1);vi.stubGlobal("cancelAnimationFrame",()=>{});
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
 const result={columns:["NAME"],rows:[["demo"]],returned_rows:1};
 const row=(event_type:string,sequence:number,metadata:Record<string,unknown>)=>({id:`i${sequence}`,event_type,sequence,metadata,kind:"tool",status:"succeeded",title:"test",timestamp:"2026-10-14T00:00:00Z"});
 const activity=runActivitySchema.parse({run_id:"inline-r",status:"running",started_at:"2026-10-14T00:00:00Z",items:[row('tool.request',1,{name:"mcp__database__query",tool_call_id:"q"}),row('tool.result',2,{tool_call_id:"q",result_preview:JSON.stringify([{type:"text",text:JSON.stringify(result)}])})]});
 try{
  await act(async()=>root.render(<ActivitySummary activity={activity}/>));
  const tool=host.querySelector<HTMLDetailsElement>('.execution-action')!;
  await act(async()=>{tool.open=true;tool.dispatchEvent(new Event('toggle'));});
  expect(tool.querySelector('.execution-action-body')).not.toBeNull();
  expect(tool.querySelector('pre code')?.textContent).toBe(JSON.stringify(result,null,2));
  expect(document.querySelector('[role="dialog"]')).toBeNull();expect(host.querySelector('table')).toBeNull();
 }finally{await act(async()=>root.unmount());host.remove();vi.unstubAllGlobals();}
});
