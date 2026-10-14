// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect,it,vi } from "vitest";
import { readFileSync } from "node:fs";
import { ActivitySummary } from "../src/components/activity-summary";
import { runActivitySchema } from "../src/lib/activity-schema";
(globalThis as Record<string,unknown>).IS_REACT_ACT_ENVIRONMENT=true;
it("keeps compact tool rows aligned and expands decoded output inline",async()=>{
 vi.stubGlobal("ResizeObserver",class {observe(){}disconnect(){}});vi.stubGlobal("requestAnimationFrame",()=>1);vi.stubGlobal("cancelAnimationFrame",()=>{});
 vi.stubGlobal("matchMedia",()=>({matches:true,addEventListener(){},removeEventListener(){}}));
 document.body.className="codex-theme-v1";
 const host=document.createElement("div");document.body.append(host);const root=createRoot(host);
 const style=document.createElement("style");style.textContent=readFileSync("src/app/codex-theme.css","utf8")+readFileSync("src/app/conversation-experience.css","utf8");document.head.append(style);
 const make=(event_type:string,sequence:number,metadata:Record<string,unknown>,summary?:string)=>({id:`e${sequence}`,event_type,sequence,metadata,summary,kind:"tool",title:"test",status:"succeeded",timestamp:"2026-10-14T00:00:00Z"});
 const output=JSON.stringify([{type:"text",text:JSON.stringify({columns:["类型","数量"],rows:[["OTHER",9809]],returned_rows:1,truncated:false})}]);
 const activity=runActivitySchema.parse({run_id:"dialog-test",status:"running",started_at:"2026-10-14T00:00:00Z",items:[make("reasoning.delta",1,{item_id:"thought"},"核验"),make("tool.request",2,{tool_call_id:"q",name:"mcp__database__query"}),make("tool.result",3,{tool_call_id:"q",result_preview:output})]});
 try {
  await act(async()=>root.render(<ActivitySummary activity={activity}/>));
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  const tool=host.querySelector<HTMLDetailsElement>(".execution-action")!;
  const thought=host.querySelector(".execution-reasoning-summary")!;
  expect(getComputedStyle(tool.querySelector("summary")!).height).toBe(getComputedStyle(thought).height);
  expect(getComputedStyle(tool.querySelector("summary")!).height).toBe("24px");
  await act(async()=>{tool.open=true;tool.dispatchEvent(new Event("toggle"));});
  expect(document.querySelector('[role="dialog"]')).toBeNull();
  expect(tool.querySelector("pre code")?.textContent).toContain('"OTHER"');
  expect(tool.querySelector(".execution-action-body")).not.toBeNull();
  await act(async()=>{tool.open=false;tool.dispatchEvent(new Event("toggle"));});
  expect(tool.open).toBe(false);
 } finally {await act(async()=>root.unmount());host.remove();style.remove();document.body.className="";vi.unstubAllGlobals();}
});
