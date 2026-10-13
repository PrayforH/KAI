// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { expect, it } from "vitest";
import { TerminalResponseNotice } from "../src/components/terminal-response-notice";
import { runActivitySchema } from "../src/lib/activity-schema";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT=true;
it("reports cancelled tool work without promoting process prose into a completed answer", async()=>{
 const host=document.createElement("div");document.body.append(host);const root=createRoot(host);
 const activity=runActivitySchema.parse({run_id:"cancelled-probe",status:"cancelled",started_at:"2026-10-13T00:00:00Z",items:[
 {id:"r",event_type:"tool.result",kind:"tool",status:"failed",title:"工具失败",timestamp:"2026-10-13T00:00:01Z",sequence:1,metadata:{tool_call_id:"t1"}},
 {id:"r2",event_type:"tool.result",kind:"tool",status:"succeeded",title:"工具完成",timestamp:"2026-10-13T00:00:02Z",sequence:2,metadata:{tool_call_id:"t2"}}]});
 try {
  await act(async()=>root.render(<TerminalResponseNotice activity={activity} hasResponse={false}/>));
  expect(host.textContent).toContain("已停止");expect(host.textContent).toContain("2 次工具结果，其中 1 次报错");expect(host.textContent).toContain("不等于已完成核实");
  await act(async()=>root.render(<TerminalResponseNotice activity={{...activity,status:"running"}} hasResponse={false}/>));expect(host.textContent).toBe("");
  await act(async()=>root.render(<TerminalResponseNotice activity={activity} hasResponse/>));expect(host.textContent).toBe("");
  await act(async()=>root.render(<TerminalResponseNotice activity={{...activity,status:"failed"}} hasResponse={false}/>));expect(host.textContent).toContain("运行失败");
  await act(async()=>root.render(<TerminalResponseNotice activity={{...activity,status:"succeeded"}} hasResponse={false}/>));expect(host.textContent).toContain("未收到可展示的最终回答");
 } finally {await act(async()=>root.unmount());host.remove();}
});
