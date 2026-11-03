// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it } from "vitest";
import { BotRunNotice } from "../src/components/bot-run-notice";
import { runActivitySchema } from "../src/lib/activity-schema";
(globalThis as Record<string,unknown>).IS_REACT_ACT_ENVIRONMENT=true;
it("uses one neutral Bot status and a real retry action without task-panel prose",async()=>{
 const host=document.createElement("div");document.body.append(host);const root=createRoot(host);
 const activity=runActivitySchema.parse({run_id:"r",status:"failed",started_at:"2026-11-03T10:00:00Z",items:[]});
 try {
  await act(async()=>root.render(<BotRunNotice activity={activity} incomplete hasResponse={false} retry={<button>重试</button>}/>));
  expect(host.textContent).toBe("回复中断重试");expect(host.querySelectorAll('[role="status"]')).toHaveLength(1);
  expect(host.querySelector(".grok-face")).not.toBeNull();expect(host.querySelector("hr")).toBeNull();
  await act(async()=>root.render(<BotRunNotice activity={{...activity,status:"cancelled"}} incomplete={false} hasResponse={false} retry={<button>重试</button>}/>));
  expect(host.textContent).toBe("已停止");expect(host.querySelector("button")).toBeNull();
  await act(async()=>root.render(<BotRunNotice activity={{...activity,status:"succeeded"}} incomplete={false} hasResponse retry={<button>重试</button>}/>));
  expect(host.textContent).toBe("");
 }finally{await act(async()=>root.unmount());host.remove();}
});
