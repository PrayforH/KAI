// @vitest-environment jsdom
import {act} from 'react';
import {createRoot} from 'react-dom/client';
import {AssistantRuntimeProvider,useExternalStoreRuntime,useAuiState} from '@assistant-ui/react';
import {expect,it} from 'vitest';
import {ConversationRedactionProvider,useConversationRedactor} from '../src/components/conversation-redaction';
(globalThis as Record<string,unknown>).IS_REACT_ACT_ENVIRONMENT=true;
function Probe(){const count=useAuiState(s=>s.thread.messages.length);const redact=useConversationRedactor();return <div data-count={count} data-turn-label={redact('密码\nExample-Pass-42!')}>{redact('收到 Example-Pass-42!')}</div>;}
function Fixture(){const runtime=useExternalStoreRuntime({messages:[{role:'user' as const,content:[{type:'text' as const,text:'密码\nExample-Pass-42!'}]}],convertMessage:(message)=>message,isRunning:false,onNew:async()=>{}});return <AssistantRuntimeProvider runtime={runtime}><span data-runtime-count={runtime.thread.getState().messages.length}/><ConversationRedactionProvider><Probe/></ConversationRedactionProvider></AssistantRuntimeProvider>;}
it('works with the real assistant runtime before Thread.Root mounts and masks existing message values',async()=>{
 const host=document.createElement('div');document.body.append(host);const root=createRoot(host);
 try{await act(async()=>root.render(<Fixture/>));expect(host.innerHTML).not.toContain('Example-Pass-42!');expect(host.textContent).toBe('收到 ••••••••');}
 finally{await act(async()=>root.unmount());host.remove();}
});
