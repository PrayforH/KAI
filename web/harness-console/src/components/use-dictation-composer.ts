"use client";

import { useRef, type RefObject } from "react";
import { DictationEdit, dictationCaret, type DictationPatch } from "../lib/dictation-edit";

export function useDictationComposer(input: RefObject<HTMLTextAreaElement | null>, read: () => string,
  write: (text: string) => void, composing: RefObject<boolean>) {
  const edit = useRef<DictationEdit | undefined>(undefined);
  const revision = useRef(0);

  function apply(patch: DictationPatch | undefined, focus = false) {
    if (!patch) return;
    const element = input.current;
    const start = dictationCaret(element?.selectionStart ?? patch.end, patch);
    const end = dictationCaret(element?.selectionEnd ?? patch.end, patch);
    const current = ++revision.current;
    write(patch.text);
    requestAnimationFrame(() => {
      if (current !== revision.current || !element || element !== input.current || element.value !== patch.text || composing.current) return;
      if (focus) element.focus();
      element.setSelectionRange(start, end);
    });
  }

  return {
    onChange(text: string) { edit.current?.observe(text); },
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
      if (!composing.current) apply(edit.current?.replace(read(), text), true);
      edit.current = undefined;
    },
  };
}
