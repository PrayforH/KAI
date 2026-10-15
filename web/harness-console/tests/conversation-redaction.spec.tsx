// @vitest-environment jsdom
import {act} from 'react';
import {createRoot} from 'react-dom/client';
import {expect,it,vi} from 'vitest';
const messages=vi.hoisted(()=>[{role:'user',content:[{type:'text',text:'数据库用户名（只读账号）\nreader/Example-Pass-42!'}]}]);
vi.mock('@assistant-ui/react',async original=>({...await original<typeof import('@assistant-ui/react')>(),useAuiState:(selector:(state:unknown)=>unknown)=>selector({thread:{messages}})}));
import {ConversationRedactionProvider,useConversationRedactor} from '../src/components/conversation-redaction';
import {SentUserContent} from '../src/components/sent-user-content';
import {SteeringUserMessage} from '../src/components/steering-user-message';
import {ConversationInputCard} from '../src/components/conversation-input-card';
import {SECRET_MASK} from '../src/lib/conversation-secrets';
(globalThis as Record<string,unknown>).IS_REACT_ACT_ENVIRONMENT=true;
function Display(){const redact=useConversationRedactor();const text=redact(messages[0].content[0].text);return <><div data-turn-label={text}><SentUserContent text={text}/></div><SteeringUserMessage text='收到 Example-Pass-42!，继续处理' status='accepted'/><ConversationInputCard input={{version:1,title:'连接 Example-Pass-42!',questions:[{id:'pwd',label:'替换 Example-Pass-42!',type:'password',options:[]}]}} disabled={false} onSubmit={()=>{}}/></>;}
it('masks history, index attributes and unlabelled echoes before the first visible render',async()=>{
 vi.stubGlobal('ResizeObserver',class{observe(){}disconnect(){}});
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
 try{await act(async()=>root.render(<ConversationRedactionProvider><Display/></ConversationRedactionProvider>));
 expect(host.innerHTML).not.toContain('Example-Pass-42!');expect(host.querySelector('[data-turn-label]')?.getAttribute('data-turn-label')).toContain(SECRET_MASK);
 expect(host.querySelector('.steering-user-message')?.textContent).toContain(SECRET_MASK);
 }finally{await act(async()=>root.unmount());host.remove();vi.unstubAllGlobals();}
});
