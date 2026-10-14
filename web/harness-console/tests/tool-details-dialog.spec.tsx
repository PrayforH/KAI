// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect,it,vi } from "vitest";
import { readFileSync } from "node:fs";
import { ActivitySummary } from "../src/components/activity-summary";
import { runActivitySchema } from "../src/lib/activity-schema";
(globalThis as Record<string,unknown>).IS_REACT_ACT_ENVIRONMENT=true;
it("opens decoded query output in a centered accessible dialog and restores the compact row on Escape",async()=>{
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
  expect(document.querySelector('[role="dialog"]')?.getAttribute("aria-modal")).toBe("true");
  expect(document.querySelector(".tool-result-table")?.textContent).toContain("OTHER9809");
  expect(host.querySelector(".execution-action-body")).toBeNull();
  const raw=[...document.querySelectorAll<HTMLButtonElement>("button")].find(b=>b.textContent==="查看原始内容")!;
  await act(async()=>raw.click());expect(document.querySelector(".execution-action-result")?.textContent).toBe(output);
  await act(async()=>document.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true})));
  expect(document.querySelector('[role="dialog"]')).toBeNull();expect(tool.open).toBe(false);
 } finally {await act(async()=>root.unmount());host.remove();style.remove();document.body.className="";vi.unstubAllGlobals();}
});
