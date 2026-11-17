// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { beforeEach, afterEach, it, expect } from "vitest";
import { MessageAttachmentGroup, type MessageAttachment } from "../src/components/message-attachment-view";
import { MarkdownText } from "../src/components/markdown-text";
import { TextMessagePartProvider } from "@assistant-ui/react";
let host:HTMLDivElement, root:Root;
beforeEach(()=>{host=document.createElement("div");document.body.append(host);root=createRoot(host);});
afterEach(()=>{act(()=>root.unmount());host.remove();});
const photo = (i:number):MessageAttachment => ({id:`input_artifact_${i}`,name:`照片${i}.jpg`,type:"image",contentType:"image/jpeg",content:[]});
it("bounds the first view, expands mixed attachments and previews them through the shared event", async()=>{
 const files=[...Array.from({length:7},(_,i)=>photo(i)),{id:"input_artifact_pdf",name:"报告.pdf",type:"document",contentType:"application/pdf",content:[]}] as MessageAttachment[];
 const seen: unknown[] = [];
 const listener = (event: Event) => seen.push((event as CustomEvent).detail);
 window.addEventListener("harness:preview-artifact", listener);
 try {
  act(()=>root.render(<MessageAttachmentGroup attachments={files}/>));
  expect(host.querySelectorAll('.message-attachment-card')).toHaveLength(6);
  expect(host.textContent).toContain("+2 个附件");
  act(()=>host.querySelector<HTMLButtonElement>('.message-attachment-expand')!.click());
  expect(host.querySelectorAll('.message-attachment-card')).toHaveLength(8);
  // Documents no longer download straight from the bubble: they open the dialog.
  expect(host.querySelector('[download="报告.pdf"]')).toBeNull();
  act(()=>host.querySelector<HTMLButtonElement>('[title="预览 报告.pdf"]')!.click());
  expect(seen.at(-1)).toMatchObject({
   artifact_id: "input_artifact_pdf",
   name: "报告.pdf",
   media_type: "application/pdf",
   content_url: "/api/input-artifacts/input_artifact_pdf/content?inline=1",
   download_url: "/api/input-artifacts/input_artifact_pdf/content",
  });
  act(()=>host.querySelector<HTMLButtonElement>('[title="预览 照片1.jpg"]')!.click());
  expect(seen.at(-1)).toMatchObject({
   artifact_id: "input_artifact_1",
   content_url: "/api/input-artifacts/input_artifact_1/content?inline=1",
  });
 } finally {
  window.removeEventListener("harness:preview-artifact", listener);
 }
});
it("leaves files the console never uploaded as plain static chips", async()=>{
 act(()=>root.render(<MessageAttachmentGroup attachments={[{id:"artifact_external",name:"外部.bin",type:"file",contentType:"application/octet-stream",content:[]}]}/>));
 expect(host.querySelector(".message-attachment-card button")).toBeNull();
 expect(host.querySelector(".message-attachment-static")).not.toBeNull();
});
it("groups adjacent Markdown photos without swallowing surrounding text", async()=>{
 const text='正文前。\n\n![横图](https://cdn.example.com/a.jpg)\n\n![竖图](https://cdn.example.com/b.jpg)\n\n正文后。';
 await act(async()=>root.render(<TextMessagePartProvider text={text} isRunning={false}><MarkdownText/></TextMessagePartProvider>));
 expect(host.querySelectorAll('.conversation-image-group img')).toHaveLength(2);
 expect(host.textContent).toContain('正文前。');expect(host.textContent).toContain('正文后。');
 expect(host.querySelector('.conversation-image-group')!.tagName).toBe('P');
});
