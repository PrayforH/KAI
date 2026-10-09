"use client";

import { useLayoutEffect, useRef, type RefObject } from "react";
import { DictationEdit, dictationCaret, type DictationPatch } from "../lib/dictation-edit";

export function useDictationComposer(input: RefObject<HTMLTextAreaElement | null>, read: () => string,
  write: (text: string) => void, composing: RefObject<boolean>) {
  const edit = useRef<DictationEdit | undefined>(undefined);
  const pending = useRef<{
    element: HTMLTextAreaElement; text: string; start: number; end: number;
    direction: "forward" | "backward" | "none"; top: number; follow: boolean; focus: boolean;
  } | undefined>(undefined);

  useLayoutEffect(() => {
    const view = pending.current;
    if (composing.current) { pending.current = undefined; return; }
    if (!view || view.element !== input.current || view.element.value !== view.text) return;
    pending.current = undefined;
    if (view.focus) view.element.focus({ preventScroll: true });
    view.element.setSelectionRange(view.start, view.end, view.direction);
    view.element.scrollTop = view.follow ? view.element.scrollHeight : view.top;
  });

  function apply(patch: DictationPatch | undefined, focus = false) {
    if (!patch) return;
    if (!focus && patch.text === read()) return;
    const element = input.current;
    if (element) {
      const queued = pending.current;
      const before = queued?.element === element ? queued : undefined;
      const start = before?.start ?? element.selectionStart;
      const end = before?.end ?? element.selectionEnd;
      pending.current = {
        element, text: patch.text,
        start: dictationCaret(start, patch), end: dictationCaret(end, patch),
        direction: before?.direction ?? element.selectionDirection,
        top: before?.top ?? element.scrollTop,
        follow: before?.follow ?? (start === end && end >= patch.start
          && element.scrollHeight - element.clientHeight - element.scrollTop <= 24),
        focus,
      };
    }
    write(patch.text);
  }

  return {
    onChange(text: string) { pending.current = undefined; edit.current?.observe(text); },
    onActive(active: boolean) {
      if (active) {
        const text = read();
        edit.current = new DictationEdit(text, input.current?.selectionStart ?? text.length, input.current?.selectionEnd ?? text.length);
      } else if (edit.current) {
        // A completed insertion clears the transaction first; only cancellation rolls back.
        if (!composing.current) apply(edit.current.cancel(read()), true);
        edit.current = undefined;
      }
    },
    onDraft(text: string) {
      if (!composing.current) apply(edit.current?.replace(read(), text));
    },
    onInsert(text: string) {
      if (!composing.current) apply(edit.current?.replace(read(), text, true), true);
      edit.current = undefined;
    },
  };
}
