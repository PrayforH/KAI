"use client";
import { useComposerRuntime, type Attachment } from "@assistant-ui/react";
import { useLayoutEffect } from "react";
import { loadTaskComposerSnapshot, persistTaskComposerSnapshot } from "../lib/task-composer-draft";
import { inputAttachmentType } from "../lib/input-attachment-adapter";

/** Save runtime changes synchronously, including the last edit before unmount. */
export function useTaskComposerSnapshot(scope: { userId: string; threadId: string } | null) {
  const composer = useComposerRuntime();
  useLayoutEffect(() => {
    if (!scope) return;
    let active = true;
    const draft = loadTaskComposerSnapshot(localStorage, scope.userId, scope.threadId);
    const remaining = [...draft.attachments];
    const persist = () => {
      const state = composer.getState();
      const ids = new Set(state.attachments.map(item => item.id));
      const waiting: Attachment[] = remaining.flatMap((item, index): Attachment[] => {
        if (item instanceof File) return [{ id: `draft-upload-${index}`, type: inputAttachmentType(item.type, item.name),
          name: item.name, file: item, contentType: item.type, status: { type: "running", reason: "uploading", progress: 0 } }];
        return item.id && ids.has(item.id) ? [] : [{ ...item, id: item.id!, type: item.type ?? "document", status: { type: "complete" } }];
      });
      persistTaskComposerSnapshot(localStorage, scope.userId, scope.threadId, state.text, [...state.attachments, ...waiting]);
    };
    if (!composer.getState().text && draft.text) composer.setText(draft.text);
    const unsubscribe = composer.subscribe(persist);
    void (async () => {
      while (remaining.length && active) {
        const attachment = remaining.shift()!;
        if (!(attachment instanceof File) && composer.getState().attachments.some(item => item.id === attachment.id)) continue;
        try { await composer.addAttachment(attachment); } catch { /* upload errors remain visible */ }
      }
      if (active) persist();
    })();
    return () => { active = false; persist(); unsubscribe(); };
  }, [composer, scope]);
}
