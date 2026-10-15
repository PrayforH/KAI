// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { TextMessagePartProvider } from "@assistant-ui/react";
import { expect, it } from "vitest";
import { MarkdownText } from "../src/components/markdown-text";
(globalThis as Record<string,unknown>).IS_REACT_ACT_ENVIRONMENT=true;
it("does not leak the over-limit form as code live or after restoration, and preserves the adjacent table", async () => {
 const host=document.createElement("div");document.body.append(host);const root=createRoot(host);
 const data={version:1,title:"确认范围",questions:[{id:"scope",label:"选择维度",type:"multi",options:Array.from({length:9},(_,i)=>`维度${i+1}`)}]};
 const before="请确认。\n\n";const block="```harness-input\n"+JSON.stringify(data)+"\n```";
 const table="\n\n|候选|状态|\n|---|---|\n|主体甲|存续|\n|主体乙|存续|";
 const render=(text:string,running:boolean)=>act(async()=>root.render(<TextMessagePartProvider text={text} isRunning={running}><MarkdownText/></TextMessagePartProvider>));
 try {
  await render(before+block.slice(0,-12),true);
  expect(host.querySelector("pre")).toBeNull();expect(host.textContent).not.toContain('"questions"');
  for(const running of [true,false]) {
   await render(before+block+table,running);
   expect(host.querySelector("pre")).toBeNull();expect(host.textContent).not.toContain('"version"');
   expect(host.textContent).toContain("维度9");expect(host.querySelector("table")?.textContent).toContain("主体甲");
  }
 } finally {await act(async()=>root.unmount());host.remove();}
});
