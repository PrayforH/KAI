"use client";

import { useEffect, useRef, useState, type ComponentProps } from "react";
import { ThreadPrimitive, useAuiEvent } from "@assistant-ui/react";
import { Composer, Thread, ThreadWelcome } from "@assistant-ui/react-ui";
import { attachConversationScroll } from "../lib/conversation-scroll";

/** A single scroll owner shared by chat, Builder and agent verification. */
export function ConversationThread({ threadId, ...config }: ComponentProps<typeof Thread> & { threadId: string }) {
  const viewport = useRef<HTMLDivElement>(null);
  const scroll = useRef<ReturnType<typeof attachConversationScroll> | null>(null);
  const [showJump, setShowJump] = useState(false);
  useEffect(() => {
    if (!viewport.current) return;
    setShowJump(false);
    const controller = attachConversationScroll(viewport.current, setShowJump);
    scroll.current = controller;
    return () => { controller.dispose(); scroll.current = null; };
  }, [threadId]);
  useAuiEvent("thread.runStart", () => scroll.current?.resume());
  const {
    Composer: ComposerComponent = Composer,
    ThreadWelcome: Welcome = ThreadWelcome,
    MessagesFooter,
    ...messageComponents
  } = config.components ?? {};
  return (
    <Thread.Root config={config}>
      <ThreadPrimitive.Viewport className="aui-thread-viewport" ref={viewport} autoScroll={false} scrollToBottomOnRunStart={false} scrollToBottomOnInitialize={false} scrollToBottomOnThreadSwitch={false}>
        <Welcome />
        <Thread.Messages MessagesFooter={MessagesFooter} components={messageComponents} />
        <Thread.FollowupSuggestions />
        <Thread.ViewportFooter>
          {showJump && <button type="button" className="aui-thread-scroll-to-bottom conversation-jump"
            aria-label="回到最新消息" title="回到最新消息" onClick={() => scroll.current?.resume()}>
            <svg viewBox="0 0 24 24" aria-hidden="true"><path d="m7 10 5 5 5-5" /></svg>
          </button>}
          <ComposerComponent />
        </Thread.ViewportFooter>
      </ThreadPrimitive.Viewport>
    </Thread.Root>
  );
}
