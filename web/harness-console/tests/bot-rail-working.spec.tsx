// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import type { Specialist } from "../src/lib/assistant-client";
import type { RunViewModel } from "../src/lib/run-view-model";
import { botIsWorking } from "../src/lib/bot-working-state";
const request = vi.hoisted(() => vi.fn());
vi.mock("../src/lib/assistant-client", () => ({assistantRequest: request}));
vi.mock("next/navigation", () => ({useRouter: () => ({push: vi.fn()})}));
vi.mock("next/link", () => ({default: ({children, scroll, ...props}: {children: React.ReactNode; scroll?: boolean}) => <a {...props}>{children}</a>}));
import { BotRail } from "../src/components/bot-rail";
import { handoverStore } from "../src/lib/handover-store";
(globalThis as Record<string,unknown>).IS_REACT_ACT_ENVIRONMENT = true;
const bot = (id: string, config = {}) => ({specialistId:id,name:id,status:"active",config,
 createdAt:"2026-11-01T00:00:00Z",updatedAt:"2026-11-01T00:00:00Z"}) as Specialist;

it("uses actual work phases and the matching live run, not availability or another task's stream", () => {
 const activity=(status:string)=>({threadId:"current",runId:"r",status});
 for(const status of ["queued","provisioning","running","cancelling"]) expect(botIsWorking(activity(status))).toBe(true);
 for(const status of ["waiting_approval","waiting_input","succeeded","failed","idle"]) expect(botIsWorking(activity(status))).toBe(false);
 const completed={runId:"r",phase:"completed"} as RunViewModel;
 expect(botIsWorking(activity("running"),"current",completed)).toBe(false);
 expect(botIsWorking(activity("running"),"another",completed)).toBe(true);
});

it("animates working main and background bots, stops on completion and keeps navigation order", async () => {
 const main="kai-personal-assistant-v1";
 const bots=[bot("specialist_primary_me"),bot("coding"),bot("weather"),bot("rest")].map((b,i)=>({...b,activity:{threadId:[main,"assistant_coding","legacy-weather","assistant_rest"][i],runId:`r${i}`,status:i===3?"succeeded":"running"}}));
 request.mockResolvedValue(bots);
 const host=document.createElement("div"),root=createRoot(host);document.body.append(host);
 const render=async()=>act(async()=>root.render(<BotRail currentThreadId={main}/>));
 try {
  await render();
  const names=()=>[...host.querySelectorAll(".bot-rail-label")].map(e=>e.textContent);
  const order=names();
  expect(host.querySelectorAll('.bot-rail-avatar[data-working="true"]')).toHaveLength(3);
  expect(host.querySelector('a[href="/assistant"] [data-working="true"]')).not.toBeNull();
  expect(host.querySelector('a[href="/assistant?bot=coding"] [data-working="true"]')).not.toBeNull();
  expect(host.querySelector('a[href="/assistant?bot=rest"] [data-working]')).toBeNull();
  request.mockResolvedValue(bots.map(b=>({...b,activity:{...b.activity,status:"succeeded"}})));
  await act(async()=>handoverStore.ingestMention({handoverId:"avatar-delivery",specialistId:"coding",specialistName:"coding",runId:"next",targetThreadId:"assistant_coding"}));
  expect(host.querySelector('[data-working="true"]')).toBeNull();
  expect(names()).toEqual(order);
  expect(request).toHaveBeenCalledTimes(2);
 } finally {await act(async()=>root.unmount());host.remove();}
});
