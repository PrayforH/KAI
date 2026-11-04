// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import type { Specialist } from "../src/lib/assistant-client";
import type { TaskSummary } from "../src/lib/task-history";
import type { RunViewModel } from "../src/lib/run-view-model";
import { workingBotThreads } from "../src/lib/bot-working-state";
const request = vi.hoisted(() => vi.fn());
vi.mock("../src/lib/assistant-client", () => ({assistantRequest: request}));
vi.mock("next/navigation", () => ({useRouter: () => ({push: vi.fn()})}));
vi.mock("next/link", () => ({default: ({children, scroll, ...props}: {children: React.ReactNode; scroll?: boolean}) => <a {...props}>{children}</a>}));
import { BotRail } from "../src/components/bot-rail";
(globalThis as Record<string,unknown>).IS_REACT_ACT_ENVIRONMENT = true;
const thread = (id: string, status: string) => ({thread_id:id,status,run_id:`run-${id}`}) as TaskSummary;
const bot = (id: string, config = {}) => ({specialistId:id,name:id,status:"active",config,
 createdAt:"2026-11-01T00:00:00Z",updatedAt:"2026-11-01T00:00:00Z"}) as Specialist;

it("uses actual work phases and the matching live run, not availability or another task's stream", () => {
 const threads=[thread("running","running"),thread("queued","queued"),thread("boot","provisioning"),
  thread("stopping","cancelling"),thread("approval","waiting_approval"),thread("input","waiting_input"),
  thread("done","succeeded"),thread("failed","failed")];
 expect([...workingBotThreads(threads,"")]).toEqual(["running","queued","boot","stopping"]);
 const completed={runId:"run-running",phase:"completed"} as RunViewModel;
 expect(workingBotThreads(threads,"running",completed).has("running")).toBe(false);
 expect(workingBotThreads(threads,"queued",completed).has("running")).toBe(true);
});

it("animates working main and background bots, stops on completion and keeps navigation order", async () => {
 const main="kai-personal-assistant-v1";
 request.mockResolvedValue([bot("specialist_primary_me"),bot("coding"),bot("weather",{conversation_thread_id:"legacy-weather"}),bot("rest")]);
 const host=document.createElement("div"),root=createRoot(host);document.body.append(host);
 const summaries=[thread(main,"running"),thread("assistant_coding","running"),thread("legacy-weather","queued"),thread("assistant_rest","succeeded"),thread("ordinary-project","running")];
 const render=async (threads: TaskSummary[])=>act(async()=>root.render(<BotRail currentThreadId={main} workingThreadIds={workingBotThreads(threads,main)}/>));
 try {
  await render(summaries);
  const names=()=>[...host.querySelectorAll(".bot-rail-label")].map(e=>e.textContent);
  const order=names();
  expect(host.querySelectorAll('.bot-rail-avatar[data-working="true"]')).toHaveLength(3);
  expect(host.querySelector('a[href="/assistant"] [data-working="true"]')).not.toBeNull();
  expect(host.querySelector('a[href="/assistant?bot=coding"] [data-working="true"]')).not.toBeNull();
  expect(host.querySelector('a[href="/assistant?bot=rest"] [data-working]')).toBeNull();
  await render(summaries.map(t=>({...t,status:"succeeded"})));
  expect(host.querySelector('[data-working="true"]')).toBeNull();
  expect(names()).toEqual(order);
  expect(request).toHaveBeenCalledTimes(1);
 } finally {await act(async()=>root.unmount());host.remove();}
});
