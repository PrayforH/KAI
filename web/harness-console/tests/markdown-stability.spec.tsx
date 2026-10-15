// @vitest-environment jsdom
import { act } from "react";
import { createRoot } from "react-dom/client";
import { TextMessagePartProvider } from "@assistant-ui/react";
import { expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { MarkdownText } from "../src/components/markdown-text";
(globalThis as Record<string,unknown>).IS_REACT_ACT_ENVIRONMENT=true;
it("keeps bold summary markup and spacing stable from streaming candidate to process commentary", async () => {
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
 const style=document.createElement('style');style.textContent=readFileSync('src/app/conversation-experience.css','utf8');document.head.append(style);document.body.className='codex-theme-v1';
 const render=(text:string,running:boolean,kind:string)=>act(async()=>root.render(<div className={kind}><TextMessagePartProvider text={text} isRunning={running}><MarkdownText stabilizeFormatting/></TextMessagePartProvider></div>));
 try {
  await render('**总结',true,'assistant-answer');
  const strong=host.querySelector('strong')!;expect(strong.textContent).toBe('总结');
  const initial=getComputedStyle(host.querySelector('.aui-md')!);const rhythm=[initial.fontSize,initial.fontWeight,initial.lineHeight,initial.whiteSpace];
  await render('**总结**\n\n下一步核验。',false,'execution-commentary');
  expect(host.querySelector('strong')?.textContent).toBe('总结');
  const final=getComputedStyle(host.querySelector('.aui-md')!);expect([final.fontSize,final.fontWeight,final.lineHeight,final.whiteSpace]).toEqual(rhythm);
  expect(final.whiteSpace).toBe('normal');expect(getComputedStyle(host.querySelector('strong')!).fontWeight).toBe('600');
  await render('**摘要',false,'execution-commentary');expect(host.querySelector('strong')?.textContent).toBe('摘要');
  await render('**摘要**',false,'execution-commentary');expect(host.querySelector('strong')?.textContent).toBe('摘要');
 } finally {await act(async()=>root.unmount());host.remove();style.remove();document.body.className='';}
});
