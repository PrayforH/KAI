"use client";
import {useEffect,useLayoutEffect,useRef,useState,type ComponentProps} from "react";
import {createPortal} from "react-dom";
import {ConversationInputCard} from "./conversation-input-card";
/** The question belongs to the answer that asked it: the card lives in that message's
 * bubble instead of a separate row above the composer, and follows the message when the
 * list re-renders. The composer holder is only the fallback for a message whose anchor
 * is not mounted. The portal keeps one container so relocation never remounts a draft. */
export function FollowupInputPlacement({messageId,...props}: ComponentProps<typeof ConversationInputCard> & {messageId:string}) {
  const holder=useRef<HTMLDivElement>(null);
  const [container,setContainer]=useState<HTMLDivElement|null>(null);
  useLayoutEffect(()=>{
    const node=document.createElement('div');node.className='followup-input-container';
    holder.current?.append(node);setContainer(node);
    return()=>node.remove();
  },[]);
  useEffect(()=>{
    if(!container)return;
    const frame=holder.current?.closest('.harness-thread-frame');
    const place=()=>{
      const anchor=[...(frame?.querySelectorAll<HTMLElement>('[data-followup-for]') ?? [])].find(node=>node.dataset.followupFor===messageId) ?? null;
      const target=anchor ?? holder.current;
      if(target && container.parentElement!==target) target.append(container);
    };
    place();
    const observer=frame?new MutationObserver(place):null;
    if(frame&&observer)observer.observe(frame,{childList:true,subtree:true});
    return()=>{observer?.disconnect();container.remove();};
  },[container,messageId]);
  return <><div ref={holder} className="followup-input-holder"/>{container&&createPortal(<ConversationInputCard {...props}/>,container)}</>;
}
