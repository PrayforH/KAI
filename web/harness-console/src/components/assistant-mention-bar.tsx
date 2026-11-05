"use client";

import { useEffect, useMemo, useRef } from "react";
import { handoverStore, liveMentionsForThread, useHandoverNotices } from "../lib/handover-store";
import { watchMentionCompletion } from "../lib/mention-completion";

/** Refresh the durable timeline on delivery and completion; never render a footer list. */
export function useMentionHistoryUpdates(threadId: string | undefined, onChange: () => void) {
  const notices = useHandoverNotices();
  useEffect(() => { if (threadId) void handoverStore.seed(); }, [threadId]);
  // Durable history already contains old receipts. Replaying every old Run's
  // SSE on mount flooded the first screen with tool traces and refreshes.
  const owned = useMemo(() => threadId ? liveMentionsForThread(notices, threadId) : [], [notices, threadId]);
  const seen = useRef(new Set<string>());
  const completed = useRef(new Set<string>());
  useEffect(() => {
    if (!threadId) return;
    const controller = new AbortController();
    const fresh = owned.filter(receipt => !seen.current.has(receipt.handoverId));
    for (const receipt of fresh) seen.current.add(receipt.handoverId);
    if (fresh.length) onChange();
    for (const receipt of owned) {
      if (!receipt.runId || completed.current.has(receipt.runId)) continue;
      const runId = receipt.runId;
      void watchMentionCompletion(runId, controller.signal, event => {
        if (event.type === "TOOL_CALL_START" && event.toolCallName === "harness_request_approval") onChange();
      }).then(terminal => {
        if (!terminal || controller.signal.aborted) return;
        completed.current.add(runId);
        onChange();
      }).catch(() => { /* Re-entering the chat recovers from durable history. */ });
    }
    return () => controller.abort();
  }, [owned, threadId, onChange]);
}
