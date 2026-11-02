// @vitest-environment jsdom
import {act} from "react";
import {createRoot, type Root} from "react-dom/client";
import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";
const request=vi.hoisted(()=>vi.fn());
vi.mock("../src/lib/assistant-client",()=>({assistantRequest:request}));
import {HandoverInlineRows} from "../src/components/handover-row";
import {AssistantNoticeSource} from "../src/components/assistant-notice-source";
import {handoverStore} from "../src/lib/handover-store";
import {emitHandover} from "./helpers/handover";
(globalThis as Record<string,unknown>).IS_REACT_ACT_ENVIRONMENT=true;
let host:HTMLDivElement, root:Root;
beforeEach(()=>{
 host=document.createElement("div");document.body.append(host);root=createRoot(host);
 request.mockReset();request.mockResolvedValue([]);
});
afterEach(async()=>{await act(async()=>root.unmount());host.remove();});
async function mount(status:string){
 await act(async()=>{
  emitHandover({handover_id:"event-"+status, specialist_id:"event-bot-"+status,specialist_name:"编码协同",status});
  root.render(<HandoverInlineRows messageId="assistant-run-ui-test"/>);
 });
}
describe("handover delivery event",()=>{
 it.each(["sent","received","applied","executing","pending_input"])("shows an identity link without mirrored controls (%s)",async status=>{
  await mount(status);
  const row=Array.from(host.querySelectorAll(".handover-inline-item")).find(node=>node.querySelector("a")?.getAttribute("href")==="/assistant?bot=event-bot-"+status)!;
  expect(row.querySelector(".assistant-event-line")?.textContent).toBe("已发送消息给编码协同");
  expect(row.querySelector("svg")).not.toBeNull();
  expect(row.querySelector("form,button,input,select,textarea,details")).toBeNull();
  expect(request.mock.calls.some(([,method])=>method&&method!=="GET")).toBe(false);
  expect(request.mock.calls.every(([path])=>["handovers","specialists"].includes(path))).toBe(true);
 });
 it("does not claim delivery success for failed or unknown receipts",async()=>{
  await mount("failed");await mount("unknown");
  expect(host.textContent).toContain("转交未完成");expect(host.textContent).toContain("待核实");
  const failed=host.querySelector('[href="/assistant?bot=event-bot-failed"]')!.closest(".assistant-event-line")!;
  expect(failed.getAttribute("role")).toBe("alert");expect(failed.textContent).not.toContain("已发送消息给");
 });
 it("reuses the source-line layout and the existing contact identity",async()=>{
  const sid="centered-bot";
  await act(async()=>{
   handoverStore.updateSpecialists([{specialistId:sid,name:"编码协同",updatedAt:"2026-11-02T10:00:00Z",config:{appearance:{color:"#d2550a",avatar:"blob"}}}]);
   emitHandover({handover_id:"centered",specialist_id:sid,specialist_name:"编码协同",status:"applied"});
   root.render(<><HandoverInlineRows messageId="assistant-run-ui-test"/><AssistantNoticeSource parts={[{type:"tool-call",toolName:"harness_notice_source",args:{name:"编码协同",botId:sid,href:"/assistant?bot="+sid,color:"#d2550a",avatar:"blob"}}]}/></>);
  });
  const outgoing=host.querySelector('[href="/assistant?bot=centered-bot"]')!.closest(".assistant-event-line")!;
  const incoming=Array.from(host.querySelectorAll(".assistant-event-line")).find(node=>node.textContent==="消息来自编码协同")!;
  expect(outgoing.className).toBe(incoming.className);
  expect(outgoing.querySelector("path")?.getAttribute("fill")).toBe(incoming.querySelector("path")?.getAttribute("fill"));
 });
 it("refreshes a renamed contact and avatar without changing receipt status",async()=>{
  await mount("pending_input");
  const before=handoverStore.snapshot().find(n=>n.handoverId==="event-pending_input")!;
  await act(async()=>handoverStore.updateSpecialists([{specialistId:before.specialistId,name:"编码伙伴",updatedAt:"2026-11-02T11:00:00Z",config:{appearance:{color:"#126fd4",avatar:"blob"}}}]));
  const after=handoverStore.snapshot().find(n=>n.handoverId===before.handoverId)!;
  expect(after.status).toBe("pending_input");expect(after.at).toBe(before.at);
  expect(host.querySelector('[href="/assistant?bot=event-bot-pending_input"]')!.textContent).toBe("编码伙伴");
 });
});
