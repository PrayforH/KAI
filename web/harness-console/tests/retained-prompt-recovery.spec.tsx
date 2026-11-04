// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it, vi } from "vitest";
import { useRetainedPromptRecovery } from "../src/components/use-retained-prompt-recovery";
const read=vi.hoisted(()=>vi.fn());
vi.mock("../src/lib/steering-poller",()=>({readSteeringState:read}));
(globalThis as Record<string,unknown>).IS_REACT_ACT_ENVIRONMENT=true;

it("cleans only the confirmed request identities, after history, without polling or replay", async () => {
 const host=document.createElement("div"),root=createRoot(host),accepted=vi.fn();
 const items=[{id:"old",text:"old",attachments:[],receipt:"unknown" as const,historyRunId:"r"},
   {id:"uncertain",text:"old",attachments:[],receipt:"unknown" as const,historyRunId:"r"},
   {id:"in-flight",text:"old",attachments:[],steerRunId:"r"}];
 read.mockResolvedValue({available:false,requests:[{request_id:"old",status:"accepted"},
   {request_id:"uncertain",status:"unknown"},{request_id:"in-flight",status:"accepted"},
   {request_id:"unrelated",status:"accepted"}]});
 function Observer({ready}:{ready:boolean}){useRetainedPromptRecovery({enabled:ready,scopeKey:"owner:thread",items,onAccepted:accepted});return null;}
 try {
  await act(async()=>root.render(<Observer ready={false}/>));expect(read).not.toHaveBeenCalled();
  await act(async()=>root.render(<Observer ready/>));
  expect(read).toHaveBeenCalledTimes(1);expect(read).toHaveBeenCalledWith("r");
  expect(accepted).toHaveBeenCalledWith(new Set(["old", "in-flight"]));
  await act(async()=>root.render(<Observer ready/>));expect(read).toHaveBeenCalledTimes(1);
 } finally {await act(async()=>root.unmount());}
});
