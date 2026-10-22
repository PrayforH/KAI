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
it("bounds the first view, expands mixed attachments and navigates original photos", async()=>{
 const files=[...Array.from({length:7},(_,i)=>photo(i)),{id:"input_artifact_pdf",name:"报告.pdf",type:"document",contentType:"application/pdf",content:[]}] as MessageAttachment[];
 act(()=>root.render(<MessageAttachmentGroup attachments={files}/>));
 expect(host.querySelectorAll('.message-attachment-card')).toHaveLength(6);
 expect(host.textContent).toContain("+2 个附件");
 act(()=>host.querySelector<HTMLButtonElement>('.message-attachment-expand')!.click());
 expect(host.querySelectorAll('.message-attachment-card')).toHaveLength(8);
 expect(host.querySelector<HTMLAnchorElement>('[download="报告.pdf"]')!.href).toContain('/api/input-artifacts/input_artifact_pdf/content');
 act(()=>host.querySelector<HTMLButtonElement>('.message-attachment-open')!.click());
 const dialog=document.querySelector('[role=dialog]')!;
 expect(dialog.textContent).toContain('1 / 7');
 act(()=>dialog.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowRight',bubbles:true})));
 expect(dialog.querySelector<HTMLImageElement>('.image-lightbox-stage img')!.alt).toBe('照片1.jpg');
 await act(async()=>document.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true})));
 expect(document.querySelector('[role=dialog]')).toBeNull();
});
it("groups adjacent Markdown photos without swallowing surrounding text", async()=>{
 const text='正文前。\n\n![横图](https://cdn.example.com/a.jpg)\n\n![竖图](https://cdn.example.com/b.jpg)\n\n正文后。';
 await act(async()=>root.render(<TextMessagePartProvider text={text} isRunning={false}><MarkdownText/></TextMessagePartProvider>));
 expect(host.querySelectorAll('.conversation-image-group img')).toHaveLength(2);
 expect(host.textContent).toContain('正文前。');expect(host.textContent).toContain('正文后。');
 expect(host.querySelector('.conversation-image-group')!.tagName).toBe('P');
});
