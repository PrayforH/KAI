"use client";

import { ComposerPrimitive } from "@assistant-ui/react";
import { forwardRef, useRef, type ComponentPropsWithoutRef } from "react";

type InputEvents = Pick<ComponentPropsWithoutRef<"textarea">, "onKeyDown" | "onCompositionStart" | "onCompositionEnd"> & {
  onComposingChange?: (composing: boolean) => void;
};
type Props = Omit<ComponentPropsWithoutRef<typeof ComposerPrimitive.Input>, "submitOnEnter" | "submitMode"> & InputEvents;

/** Shared by the runtime composer and the project launch draft. */
function useInputEvents({ onKeyDown, onCompositionStart, onCompositionEnd, onComposingChange }: InputEvents): InputEvents {
  const composing = useRef(false);
  const endedAt = useRef(-Infinity);
  return {
    onCompositionStart(event) {
      composing.current = true;
      onComposingChange?.(true);
      onCompositionStart?.(event);
    },
    onCompositionEnd(event) {
      composing.current = false;
      endedAt.current = Date.now();
      onComposingChange?.(false);
      onCompositionEnd?.(event);
    },
    onKeyDown(event) {
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
    },
  };
}

/** Keep the primitive's synchronous input updates; IME Enter never selects or sends. */
export const ConversationInput = forwardRef<HTMLTextAreaElement, Props>(function ConversationInput(
  { onKeyDown, onCompositionStart, onCompositionEnd, onComposingChange, ...props }, ref,
) {
  const events = useInputEvents({ onKeyDown, onCompositionStart, onCompositionEnd, onComposingChange });
  return <ComposerPrimitive.Input {...props} {...events} ref={ref} submitMode="none" cancelOnEscape={false}
    unstable_focusOnRunStart={false} unstable_focusOnScrollToBottom={false} />;
});

/** A project only collects a draft; the main conversation owns task execution. */
export const ConversationDraftInput = forwardRef<HTMLTextAreaElement, ComponentPropsWithoutRef<"textarea"> & InputEvents>(function ConversationDraftInput(
  { onKeyDown, onCompositionStart, onCompositionEnd, onComposingChange, ...props }, ref,
) {
  const events = useInputEvents({ onKeyDown, onCompositionStart, onCompositionEnd, onComposingChange });
  return <textarea {...props} {...events} ref={ref} />;
});
