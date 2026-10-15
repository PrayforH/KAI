// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect,it,vi } from "vitest";
import { responseTextOutsideProcess } from "../src/lib/process-response";
import { responseTextForTurn, AssistantResponse } from "../src/components/agent-thread";
import { ActivitySummary } from "../src/components/activity-summary";
import { runActivitySchema } from "../src/lib/activity-schema";
(globalThis as Record<string,unknown>).IS_REACT_ACT_ENVIRONMENT=true;
const text="视图全列取数超时，改用精简列重试；同时检索案件主体表中的企业类主体。";
const item=(event_type:string,sequence:number,summary?:string)=>({id:`e${sequence}`,event_type,sequence,summary,kind:"tool" as const,status:"succeeded",title:"test",timestamp:"2026-10-14T00:00:00Z",metadata:{message_id:"assistant-r",tool_call_id:`t${sequence}`,name:"Read"}});
const activity=runActivitySchema.parse({run_id:"ownership-r",status:"running",started_at:"2026-10-14T00:00:00Z",items:[item("tool.request",1),item("message.delta",2,text),item("tool.request",3)]});
it("renders a progress block once even while the live response still holds its visible copy",async()=>{
 vi.stubGlobal("ResizeObserver",class{observe(){}disconnect(){}});vi.stubGlobal("requestAnimationFrame",()=>1);vi.stubGlobal("cancelAnimationFrame",()=>{});vi.stubGlobal("matchMedia",()=>({matches:true,addEventListener(){},removeEventListener(){}}));
 const host=document.createElement("div");document.body.append(host);const root=createRoot(host);
 try{
  const captured={current:undefined as {messageId:string;text:string}|undefined};
  const candidate=responseTextForTurn("assistant-r",text,true,{text,visible:true,status:"streaming"},captured);
  // This state is observable between the activity and live-store notifications.
  expect(candidate).toBe(text);
  await act(async()=>root.render(<><ActivitySummary activity={activity}/><AssistantResponse text={responseTextOutsideProcess(candidate,activity)} streaming/></>));
  expect(host.querySelector('[data-commentary-source="progress"]')?.textContent).toBe(text);
  expect(host.querySelector(".assistant-answer")).toBeNull();
  expect(host.textContent?.split(text).length).toBe(2);
 }finally{await act(async()=>root.unmount());host.remove();vi.unstubAllGlobals();}
});
it("preserves new answers, repeated phrases after tools, and answers without a process projection",()=>{
 expect(responseTextOutsideProcess("新的最终回答",activity)).toBe("新的最终回答");
 expect(responseTextOutsideProcess(text,{...activity,items:[...activity.items,item("message.delta",4,text)]})).toBe(text);
 expect(responseTextOutsideProcess(text)).toBe(text);
});

it("hides the old preface while activity has received a different new answer before the live cache catches up",()=>{
 expect(responseTextOutsideProcess(text,{...activity,items:[...activity.items,item("message.delta",4,"新的结论正在生成")] })).toBe("");
});
