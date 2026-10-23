// @vitest-environment jsdom
import {act} from 'react';
import {createRoot} from 'react-dom/client';
import {expect,it,vi} from 'vitest';
import {SentUserContent} from '../src/components/sent-user-content';
import {redactConversationText,SECRET_MASK} from '../src/lib/conversation-secrets';
(globalThis as Record<string,unknown>).IS_REACT_ACT_ENVIRONMENT=true;
it('never mounts the raw native content renderer for a masked historical message',async()=>{
 vi.stubGlobal('ResizeObserver',class{observe(){}disconnect(){}});
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
 try{
  const raw='密码\nExample-Pass-42!';
  await act(async()=>root.render(<SentUserContent text={redactConversationText(raw)}/>));
  expect(host.textContent).toBe('密码\n'+SECRET_MASK);expect(host.innerHTML).not.toContain('Example-Pass-42!');
 }finally{await act(async()=>root.unmount());host.remove();vi.unstubAllGlobals();}
});
