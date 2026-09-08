"use client";

import { ComposerPrimitive } from "@assistant-ui/react";
import { forwardRef, useRef, type ComponentPropsWithoutRef } from "react";

type Props = Omit<ComponentPropsWithoutRef<typeof ComposerPrimitive.Input>, "submitOnEnter" | "submitMode"> & {
  onComposingChange?: (composing: boolean) => void;
};

/** Keep the primitive's synchronous input updates; IME Enter never selects or sends. */
export const ConversationInput = forwardRef<HTMLTextAreaElement, Props>(function ConversationInput(
  { onKeyDown, onCompositionStart, onCompositionEnd, onComposingChange, ...props }, ref,
) {
  const composing = useRef(false);
  const endedAt = useRef(-Infinity);
  return <ComposerPrimitive.Input {...props} ref={ref} submitMode="none" cancelOnEscape={false}
    unstable_focusOnRunStart={false} unstable_focusOnScrollToBottom={false}
    onCompositionStart={(event) => {
      composing.current = true;
      onComposingChange?.(true);
      onCompositionStart?.(event);
    }}
    onCompositionEnd={(event) => {
      composing.current = false;
      endedAt.current = Date.now();
      onComposingChange?.(false);
      onCompositionEnd?.(event);
    }}
    onKeyDown={(event) => {
      if (composing.current || event.nativeEvent.isComposing || event.keyCode === 229) {
        event.stopPropagation();
        return;
      }
      if (event.key === "Enter" && Date.now() - endedAt.current < 80) {
        event.preventDefault();
        event.stopPropagation();
        return;
      }
      onKeyDown?.(event);
    }}
  />;
});
