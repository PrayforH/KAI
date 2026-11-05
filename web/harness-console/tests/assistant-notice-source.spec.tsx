// @vitest-environment jsdom
import {renderToStaticMarkup} from "react-dom/server";
import {describe, expect, it} from "vitest";
import {AssistantNoticeSource, noticeSource} from "../src/components/assistant-notice-source";
const parts=[{type:"tool-call",toolName:"harness_notice_source",args:{name:"编码协同",href:"/assistant?bot=specialist_coding&run=run_source",color:"#f68b20",avatar:"blob"}}];
describe("source attribution",()=>{
 it("shows only the source avatar and name with a real conversation link",()=>{
  const host=document.createElement("div");host.innerHTML=renderToStaticMarkup(<AssistantNoticeSource parts={parts}/>);
  expect(host.textContent).toBe("消息来自编码协同");
  expect(host.querySelector("a")?.getAttribute("href")).toBe(parts[0].args.href);
  expect(host.querySelector("svg")).not.toBeNull();
  expect(host.textContent).not.toMatch(/specialist_|run_|回到原对话/);
 });
 it("rejects missing, malformed and external source records",()=>{
  expect(noticeSource([])).toBeNull();
  for(const href of ["https://example.com","//example.com","javascript:alert(1)","/assistant/other"])
   expect(noticeSource([{...parts[0],args:{...parts[0].args,href}}])).toBeNull();
 });
 it("links a dispatched assignment back to the main assistant",()=>{
  const mainParts=[{...parts[0],args:{name:"主助理",href:"/assistant",direction:"received"}}];
  const host=document.createElement("div");
  host.innerHTML=renderToStaticMarkup(<AssistantNoticeSource parts={mainParts}/>);
  expect(host.textContent).toBe("消息来自主助理");
  expect(host.querySelector("a")?.getAttribute("href")).toBe("/assistant");
 });
});
it("renders sent events with the same centered identity line and no reply body", () => {
 const host=document.createElement("div");
 host.innerHTML=renderToStaticMarkup(<AssistantNoticeSource parts={[{...parts[0],args:{...parts[0].args,direction:"sent"}}]}/>);
 expect(host.textContent).toBe("已发送消息给编码协同");
 expect(host.querySelector("a")?.getAttribute("href")).toBe(parts[0].args.href);
 expect(host.querySelector(".assistant-answer")).toBeNull();
});
