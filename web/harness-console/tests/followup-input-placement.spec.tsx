// @vitest-environment jsdom
import {act} from 'react';
import {createRoot,type Root} from 'react-dom/client';
import {afterEach,expect,it,vi} from 'vitest';
import {FollowupInputPlacement} from '../src/components/followup-input-placement';
import {FOLLOWUP_IDLE_MS} from '../src/components/conversation-input-card';
(globalThis as Record<string,unknown>).IS_REACT_ACT_ENVIRONMENT=true;

const roots:Root[]=[];
afterEach(()=>{for(const root of roots.splice(0))act(()=>root.unmount());document.body.replaceChildren();vi.useRealTimers();});

const input={version:1 as const,title:'补充连接',questions:[{id:'pwd',label:'数据库密码',type:'password' as const,options:[]}]};

async function mount(host:HTMLElement,messageId='question-a'){
 const root=createRoot(host);roots.push(root);
 await act(async()=>root.render(<FollowupInputPlacement messageId={messageId} input={input} disabled={false} onSubmit={()=>{}}/>));
}

it('renders the question in its own answer from the start, keeps the masked draft and never sends on its own',async()=>{
 vi.useFakeTimers();
 const host=document.createElement('div');document.body.append(host);
 const root=createRoot(host);roots.push(root);const send=vi.fn();
 await act(async()=>root.render(<div className='harness-thread-frame'><article data-followup-for='question-a'/><footer><FollowupInputPlacement messageId='question-a' input={input} disabled={false} onSubmit={send}/></footer></div>));
 const anchor=host.querySelector('[data-followup-for]')!;
 const container=host.querySelector('.followup-input-container')!;
 expect(anchor.contains(container)).toBe(true);
 expect(host.querySelector('footer .followup-input-container')).toBeNull();
 const field=anchor.querySelector<HTMLInputElement>('input[type=password]')!;
 act(()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(field,'Example-Pass-42!');field.dispatchEvent(new Event('input',{bubbles:true}));});
 act(()=>vi.advanceTimersByTime(FOLLOWUP_IDLE_MS-1));
 expect(anchor.querySelector('.followup-input-container')).toBe(container);
 expect(send).not.toHaveBeenCalled();
 act(()=>vi.advanceTimersByTime(1));
 expect(anchor.querySelector('.followup-input-container')).toBe(container);
 expect(send).not.toHaveBeenCalled();
 const collapsed=anchor.querySelector<HTMLButtonElement>('button[aria-expanded="false"]');
 if(collapsed)await act(async()=>collapsed.click());
 expect(anchor.querySelector<HTMLInputElement>('input[type=password]')!.value).toBe('Example-Pass-42!');
 const submit=[...anchor.querySelectorAll<HTMLButtonElement>('button')].find(button=>button.textContent?.includes('提交回答'))!;
 await act(async()=>submit.click());
 expect(send).toHaveBeenCalledWith('补充连接\n\n数据库密码\nExample-Pass-42!');
 expect(anchor.querySelector<HTMLInputElement>('input[type=password]')!.value).toBe('');
});

it('follows the answer when the message list re-renders it',async()=>{
 const host=document.createElement('div');const frame=document.createElement('div');frame.className='harness-thread-frame';
 const first=document.createElement('div');first.dataset.followupFor='m1';const composer=document.createElement('div');
 frame.append(first,composer);host.append(frame);document.body.append(host);
 await mount(composer,'m1');
 expect(first.querySelector('.followup-input-container')).not.toBeNull();
 const second=document.createElement('div');second.dataset.followupFor='m1';
 frame.replaceChild(second,first);
 await act(async()=>{await new Promise(resolve=>setTimeout(resolve,0));});
 expect(second.querySelector('.followup-input-container')).not.toBeNull();
 expect(composer.querySelector('.followup-input-container')).toBeNull();
});

it('keeps the card by the composer when the answer has no anchor yet',async()=>{
 const host=document.createElement('div');const frame=document.createElement('div');frame.className='harness-thread-frame';
 const composer=document.createElement('div');frame.append(composer);host.append(frame);document.body.append(host);
 await mount(composer,'m1');
 expect(composer.querySelector('.followup-input-container')).not.toBeNull();
 expect(document.querySelector('[data-followup-for]')).toBeNull();
});

it('only moves the card into its own message',async()=>{
 const host=document.createElement('div');const frame=document.createElement('div');frame.className='harness-thread-frame';
 const other=document.createElement('div');other.dataset.followupFor='m2';const composer=document.createElement('div');
 frame.append(other,composer);host.append(frame);document.body.append(host);
 await mount(composer,'m1');
 expect(other.querySelector('.followup-input-container')).toBeNull();
 expect(composer.querySelector('.followup-input-container')).not.toBeNull();
});
