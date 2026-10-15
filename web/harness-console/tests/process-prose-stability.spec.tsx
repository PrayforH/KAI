// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { readFileSync } from "node:fs";
import { expect,it,vi } from "vitest";
import { ActivitySummary } from "../src/components/activity-summary";
import { AssistantResponse } from "../src/components/agent-thread";
import { runActivitySchema } from "../src/lib/activity-schema";
import { responseTextForDisplay } from "../src/lib/process-response";
(globalThis as Record<string,unknown>).IS_REACT_ACT_ENVIRONMENT=true;
const phrase="先核对证据，再生成报告。";
const item=(type:string,sequence:number,summary?:string)=>({id:`p${sequence}`,event_type:type,sequence,summary,kind:"analysis" as const,title:"test",status:"succeeded",timestamp:"2026-10-14T00:00:00Z",metadata:type.startsWith('tool.')?{tool_call_id:'q',name:'Read'}:{item_id:'thought',message_id:'assistant-r'}});
const activity=runActivitySchema.parse({run_id:'stable-prose',status:'running',started_at:'2026-10-14T00:00:00Z',items:[item('reasoning.delta',1,'核验'),item('message.delta',2,phrase)]});
it("streams bright prose in the same process node before and after a tool, then presents the confirmed final answer",async()=>{
 vi.stubGlobal('ResizeObserver',class{observe(){}disconnect(){}});vi.stubGlobal('requestAnimationFrame',()=>1);vi.stubGlobal('cancelAnimationFrame',()=>{});vi.stubGlobal('matchMedia',()=>({matches:false,addEventListener(){},removeEventListener(){}}));
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
 const style=document.createElement('style');style.textContent=readFileSync('src/app/codex-theme.css','utf8')+readFileSync('src/app/conversation-experience.css','utf8');document.head.append(style);document.body.className='codex-theme-v1';
 const render=(a:typeof activity,text:string)=>act(async()=>root.render(<><ActivitySummary activity={a}/><AssistantResponse text={responseTextForDisplay(text,a)} streaming={a.status==='running'}/></>));
 try{
  await render(activity,phrase);
  const prose=host.querySelector('[data-commentary-source="progress"]')!;const paragraph=prose.querySelector('p');
  expect(prose.textContent).toBe(phrase);expect(host.querySelector('.assistant-answer')).toBeNull();
  const before=getComputedStyle(prose.querySelector('.aui-md')!).color;
  expect(getComputedStyle(host.querySelector('.execution-reasoning-label')!).animation).toBe('none');
  const withTool={...activity,items:[...activity.items,item('tool.request',3)]};await render(withTool,phrase);
  expect(host.querySelector('[data-commentary-source="progress"]')).toBe(prose);expect(prose.querySelector('p')).toBe(paragraph);
  expect(getComputedStyle(prose.querySelector('.aui-md')!).color).toBe(before);
  expect(host.querySelector('.assistant-answer')).toBeNull();expect(host.textContent?.split(phrase).length).toBe(2);
  const final='已完成核验，以下是报告。';const streaming={...withTool,items:[...withTool.items,item('message.delta',4,final)]};await render(streaming,final);
  expect(host.querySelector('.assistant-answer')).toBeNull();expect(host.querySelectorAll('[data-commentary-source="progress"]')).toHaveLength(2);
  const completed={...streaming,status:'succeeded',items:[...streaming.items,item('run.succeeded',5)]};await render(completed,final);
  expect(host.querySelector('.assistant-answer')?.textContent).toBe(final);
  expect([...host.querySelectorAll('[data-commentary-source="progress"]')].map(n=>n.textContent)).toEqual([phrase]);
 }finally{await act(async()=>root.unmount());host.remove();style.remove();document.body.className='';vi.unstubAllGlobals();}
});
it("preserves standalone/legacy answers and partial answers on manual stop",()=>{
 expect(responseTextForDisplay('回答')).toBe('回答');
 expect(responseTextForDisplay(phrase,{...activity,status:'cancelled'})).toBe(phrase);
 expect(responseTextForDisplay(phrase,activity)).toBe('');
});
