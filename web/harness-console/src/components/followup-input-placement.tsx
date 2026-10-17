"use client";
import {useCallback,useLayoutEffect,useRef,useState,type ComponentProps} from "react";
import {createPortal} from "react-dom";
import {ConversationInputCard} from "./conversation-input-card";
/** Keep one portal/container so relocation never remounts the answer draft. */
export function FollowupInputPlacement({messageId,...props}: ComponentProps<typeof ConversationInputCard> & {messageId:string}) {
  const holder=useRef<HTMLDivElement>(null);
  const [container,setContainer]=useState<HTMLDivElement|null>(null);
  const [dormant,setDormant]=useState(false);
  useLayoutEffect(()=>{
    const node=document.createElement('div');node.className='followup-input-container';
    holder.current?.append(node);setContainer(node);
    return()=>node.remove();
  },[]);
  useLayoutEffect(()=>{
    if(!container)return;
    const frame=holder.current?.closest('.harness-thread-frame');
    const anchor=dormant ? [...(frame?.querySelectorAll<HTMLElement>('[data-followup-for]') ?? [])].find(node=>node.dataset.followupFor===messageId):null;
    (anchor ?? holder.current)?.append(container);
  },[container,dormant,messageId]);
  const onDormant=useCallback(()=>setDormant(true),[]);
  return <><div ref={holder} className="followup-input-holder"/>{container&&createPortal(<ConversationInputCard {...props} onDormant={onDormant}/>,container)}</>;
}
