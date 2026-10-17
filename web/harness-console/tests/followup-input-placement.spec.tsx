// @vitest-environment jsdom
import {act} from 'react';
import {createRoot} from 'react-dom/client';
import {expect,it,vi} from 'vitest';
import {FollowupInputPlacement} from '../src/components/followup-input-placement';
import {FOLLOWUP_IDLE_MS} from '../src/components/conversation-input-card';
(globalThis as Record<string,unknown>).IS_REACT_ACT_ENVIRONMENT=true;
it('moves an idle form into its original turn without losing the masked answer draft or sending automatically',async()=>{
 vi.useFakeTimers();const host=document.createElement('div');document.body.append(host);const root=createRoot(host);const send=vi.fn();
 const input={version:1 as const,title:'补充连接',questions:[{id:'pwd',label:'数据库密码',type:'password' as const,options:[]}]};
 try{
  await act(async()=>root.render(<div className='harness-thread-frame'><article data-followup-for='question-a'/><footer><FollowupInputPlacement messageId='question-a' input={input} disabled={false} onSubmit={send}/></footer></div>));
  const container=host.querySelector('.followup-input-container');const field=host.querySelector<HTMLInputElement>('input[type=password]')!;
  act(()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(field,'Example-Pass-42!');field.dispatchEvent(new Event('input',{bubbles:true}));});
  act(()=>vi.advanceTimersByTime(FOLLOWUP_IDLE_MS-1));expect(host.querySelector('footer section')).not.toBeNull();
  act(()=>field.dispatchEvent(new KeyboardEvent('keydown',{key:'ArrowLeft',bubbles:true})));
  act(()=>vi.advanceTimersByTime(FOLLOWUP_IDLE_MS-1));expect(host.querySelector('footer section')).not.toBeNull();
  act(()=>vi.advanceTimersByTime(1));const anchor=host.querySelector('[data-followup-for]')!;
  expect(anchor.querySelector('.followup-input-container')).toBe(container);expect(host.querySelector('footer section')).toBeNull();
  expect(anchor.querySelector('fieldset')).toBeNull();expect(send).not.toHaveBeenCalled();
  await act(async()=>anchor.querySelector<HTMLButtonElement>('button[aria-expanded]')!.click());
  expect(anchor.querySelector<HTMLInputElement>('input[type=password]')!.value).toBe('Example-Pass-42!');
  const submit=[...anchor.querySelectorAll<HTMLButtonElement>('button')].find(button=>button.textContent?.includes('提交回答'))!;
  await act(async()=>submit.click());expect(send).toHaveBeenCalledWith('补充连接\n\n数据库密码\nExample-Pass-42!');
  expect(anchor.querySelector<HTMLInputElement>('input[type=password]')!.value).toBe('');
 }finally{await act(async()=>root.unmount());host.remove();vi.useRealTimers();}
});
