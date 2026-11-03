// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { beforeEach, afterEach, expect, it, vi } from "vitest";
const request=vi.fn(), summary=vi.fn(), save=vi.fn(), ingest=vi.fn();
vi.mock("../src/lib/assistant-client",()=>({assistantRequest:(...args:unknown[])=>request(...args),saveSpecialistCredential:(...args:unknown[])=>save(...args)}));
vi.mock("../src/lib/project-space",()=>({loadTaskSummary:(...args:unknown[])=>summary(...args)}));
vi.mock("../src/lib/handover-store",()=>({handoverStore:{ingestMention:(...args:unknown[])=>ingest(...args)}}));
import { BotConversationControls } from "../src/components/bot-conversation-controls";
import { ConversationSurfaceProvider } from "../src/components/assistant-bot-context";
(globalThis as Record<string,unknown>).IS_REACT_ACT_ENVIRONMENT=true;
let host:HTMLDivElement,root:Root,fetcher:ReturnType<typeof vi.fn>,questions:unknown[],bot:Record<string,unknown>;
const target={specialistId:"specialist-test",specialistName:"资料Bot",runId:"child-1",threadId:"assistant_child",sourceThreadId:"kai-personal-assistant-v1"};
async function flush(){for(let i=0;i<8;i++)await act(async()=>{await Promise.resolve();});}
async function mount(){await act(async()=>root.render(<ConversationSurfaceProvider surface="assistant"><BotConversationControls {...target}/></ConversationSurfaceProvider>));await flush();}
function fill(input:HTMLInputElement,value:string){Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value")!.set!.call(input,value);input.dispatchEvent(new Event("input",{bubbles:true}));}
function button(text:string){return [...host.querySelectorAll("button")].find(b=>b.textContent?.includes(text))!;}
beforeEach(()=>{
 request.mockReset();summary.mockReset();save.mockReset();ingest.mockReset();
 host=document.createElement("div");document.body.append(host);root=createRoot(host);
 questions=[{id:"scope",label:"核对范围",type:"text"}];
 bot={specialistId:target.specialistId,name:target.specialistName,revision:1,config:{}};
 request.mockImplementation(async(path:string)=>path.endsWith("/messages")?{handoverId:"h-reply",specialistId:target.specialistId,runId:"continued",sourceThreadId:target.sourceThreadId,targetThreadId:target.threadId,createdAt:new Date().toISOString()}:{specialist:bot});
 summary.mockResolvedValue({run_id:"child-1",status:"succeeded"});
 fetcher=vi.fn(async()=>Response.json({run_id:"child-1",messages:[{id:"question",role:"assistant",content:'```harness-input\n'+JSON.stringify({version:1,title:"确认范围",questions})+'\n```'}]}));
 vi.stubGlobal("fetch",fetcher);
});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();vi.unstubAllGlobals();});
it("answers in the main chat and binds the continuation back to its source",async()=>{
 await mount();expect(host.textContent).toContain("核对范围");
 await act(async()=>fill(host.querySelector('input[aria-label="核对范围 · 填写回答"]')!,"仅武汉"));
 await act(async()=>button("提交回答").click());await flush();
 const sent=request.mock.calls.find(c=>String(c[0]).endsWith("/messages"))!;
 expect(sent[2]).toMatchObject({sourceThreadId:target.sourceThreadId,replyToRunId:"child-1"});
 expect(sent[2].text).toContain("仅武汉");expect(typeof sent[3]).toBe("string");
 expect(ingest).toHaveBeenCalledWith(expect.objectContaining({runId:"continued",sourceThreadId:target.sourceThreadId}));
 expect(host.textContent).toBe("");
});
it("hides stale questions after a later child message",async()=>{
 summary.mockResolvedValue({run_id:"child-2",status:"succeeded"});await mount();expect(host.textContent).toBe("");
});
it("uses the original operation approval rather than converting it to a chat answer",async()=>{
 summary.mockResolvedValue({run_id:"child-1",status:"waiting_approval",pending_approval:{approval_id:"approval-real",run_id:"child-1",status:"pending",tool_name:"Bash",reason:"确认",argument_summary:{command:"echo hello"}}});
 fetcher.mockImplementation(async(url:string)=>url.includes("/approvals/")?new Response(null,{status:200}):Response.json({run_id:"child-1",messages:[]}));
 await mount();expect(host.textContent).toContain("echo hello");
 await act(async()=>button("允许").click());await flush();
 expect(fetcher).toHaveBeenCalledWith("/api/harness/approvals/approval-real",expect.objectContaining({method:"PUT",body:JSON.stringify({decision:"approved"})}));
 expect(request.mock.calls.some(c=>String(c[0]).endsWith("/messages"))).toBe(false);
});
it("saves secrets through the credential channel and sends only a reference",async()=>{
 questions=[{id:"site_password",label:"网站密码",type:"password"}];
 save.mockResolvedValue({...bot,revision:2,config:{"credential_ref:site_password":"opaque"}});
 await mount();await act(async()=>fill(host.querySelector('input[type="password"]')!,"Synthetic-Only-42"));
 await act(async()=>button("保存凭据").click());await flush();
 expect(save).toHaveBeenCalledWith(target.specialistId,expect.objectContaining({name:"site_password",secret:"Synthetic-Only-42"}));
 await act(async()=>button("凭证已保存，继续").click());await flush();
 const sent=request.mock.calls.find(c=>String(c[0]).endsWith("/messages"))!;
 expect(sent[2].text).toContain("site_password");expect(JSON.stringify(sent)).not.toContain("Synthetic-Only-42");
});
