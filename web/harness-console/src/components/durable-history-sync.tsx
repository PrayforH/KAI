"use client";

import { useEffect } from "react";
import { useThreadRuntime, useAuiState } from "@assistant-ui/react";
import { useRunViewModel } from "../lib/activity-store";
import { liveResponseStore } from "../lib/live-response-store";
import type { createThreadHistoryAdapter } from "../lib/task-history";

export function DurableHistorySync({
  threadId,
  revision,
  history,
  onSettled,
}: {
  threadId: string;
  revision: number;
  history: ReturnType<typeof createThreadHistoryAdapter>;
  onSettled?: () => void;
}) {
  const thread = useThreadRuntime();
  const running = useAuiState((state) => state.thread.isRunning);
  const view = useRunViewModel();
  const needsRecovery = !running && ["running", "queued", "waiting_approval"].includes(view?.phase ?? "");
  useEffect(() => {
    if (!needsRecovery) return;
    let disposed = false;
    let pending = false;
    async function refresh() {
      if (pending || thread.getState().isRunning) return;
      pending = true;
      try {
        await history.loadSnapshot((repository) => {
          if (!disposed && !thread.getState().isRunning) {
            thread.import(repository);
            liveResponseStore.clear(threadId);
          }
        });
      } catch (error) {
        if (!disposed) console.error("[Harness Console] Failed to recover active task", error);
      } finally { pending = false; }
    }
    const timer = window.setInterval(() => void refresh(), 1500);
    void refresh();
    return () => { disposed = true; window.clearInterval(timer); };
  }, [history, needsRecovery, thread, threadId]);

  useEffect(() => {
    // A completion callback can arrive before assistant-ui releases isRunning.
    // Wait for that transition instead of dropping the only terminal refresh.
    if (running) return;
    let disposed = false;
    // One frame is enough to let the runtime finish mounting; a longer wait left
    // the conversation area visibly empty after switching tasks.
    const timer = window.setTimeout(() => {
      void history
        .loadSnapshot((repository) => {
          if (!disposed && repository && !thread.getState().isRunning) {
            thread.import(repository);
            liveResponseStore.clear(threadId);
          }
        })
        .finally(() => { if (!disposed) onSettled?.(); })
        .catch((error: unknown) => {
          if (!disposed) {
            console.error(
              "[Harness Console] Failed to refresh durable history",
              error,
            );
          }
        });
    }, 16);
    return () => {
      disposed = true;
      window.clearTimeout(timer);
    };
  }, [history, revision, running, thread, threadId]);

  return null;
}
