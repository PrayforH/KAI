"use client";

import { useEffect, useRef, type ComponentProps } from "react";
import { ThreadPrimitive, useAuiEvent } from "@assistant-ui/react";
import { Composer, Thread, ThreadWelcome } from "@assistant-ui/react-ui";
import { attachConversationScroll } from "../lib/conversation-scroll";

/** A single scroll owner shared by chat, Builder and agent verification. */
export function ConversationThread({ threadId, ...config }: ComponentProps<typeof Thread> & { threadId: string }) {
  const viewport = useRef<HTMLDivElement>(null);
  const scroll = useRef<ReturnType<typeof attachConversationScroll> | null>(null);
  useEffect(() => {
    if (!viewport.current) return;
    const controller = attachConversationScroll(viewport.current);
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
          <Thread.ScrollToBottom />
          <ComposerComponent />
        </Thread.ViewportFooter>
      </ThreadPrimitive.Viewport>
    </Thread.Root>
  );
}
