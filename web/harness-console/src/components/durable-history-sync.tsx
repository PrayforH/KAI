"use client";

import { useEffect } from "react";
import { flushSync } from "react-dom";
import { useThreadRuntime, useAuiState } from "@assistant-ui/react";
import { useRunViewModel } from "../lib/activity-store";
import { liveResponseStore } from "../lib/live-response-store";
import { canHandoffResponse, retainEarlierTurns } from "../lib/response-history-handoff";
import type { createThreadHistoryAdapter } from "../lib/task-history";

export function DurableHistorySync({ threadId, revision, history, onSettled }: {
  threadId: string;
  revision: number;
  history: ReturnType<typeof createThreadHistoryAdapter>;
  onSettled?: () => void;
}) {
  const thread = useThreadRuntime();
  const running = useAuiState(state => state.thread.isRunning);
  const view = useRunViewModel();
  const needsRecovery = !running && ["running", "queued", "waiting_approval"].includes(view?.phase ?? "");

  useEffect(() => {
    if (running) return;
    let disposed = false;
    let timer: number;
    let expectedRun = liveResponseStore.getSnapshot().runId;
    // One serial refresh loop covers both stop recovery and terminal hand-off.
    // Never clear the live answer until durable history contains that answer.
    async function refresh() {
      let accepted = false;
      try {
        await history.loadSnapshot(repository => {
          const live = liveResponseStore.getSnapshot();
          if (disposed || thread.getState().isRunning || live.runId !== expectedRun ||
              !canHandoffResponse(repository, live)) return false;
          const merged = retainEarlierTurns(repository, thread.getState().messages);
          flushSync(() => {
            thread.import(merged);
            liveResponseStore.clear(threadId);
            expectedRun = undefined;
          });
          accepted = true;
          return true;
        });
      } catch (error) {
        if (!disposed) console.error("[Harness Console] Failed to refresh durable history", error);
      }
      if (disposed) return;
      onSettled?.();
      // Cancel can finish locally before the server commits its last events.
      if (!accepted || needsRecovery) timer = window.setTimeout(() => void refresh(), 1500);
    }
    timer = window.setTimeout(() => void refresh(), 16);
    return () => { disposed = true; window.clearTimeout(timer); };
  }, [history, revision, running, needsRecovery, thread, threadId]);

  return null;
}
