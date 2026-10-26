"use client";

import { useEffect } from "react";
import { flushSync } from "react-dom";
import { useThreadRuntime, useAuiState } from "@assistant-ui/react";
import { useRunViewModel } from "../lib/activity-store";
import { liveResponseStore } from "../lib/live-response-store";
import { canHandoffResponse, retainEarlierTurns } from "../lib/response-history-handoff";
import type { createThreadHistoryAdapter } from "../lib/task-history";
import { PERSONAL_ASSISTANT_THREAD } from "./personal-assistant-context";

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
  const watchExternal = threadId === PERSONAL_ASSISTANT_THREAD;

  useEffect(() => {
    if (running) return;
    let disposed = false;
    let timer: number;
    let expectedRun = liveResponseStore.getSnapshot().runId;
    let lastImported = "";
    let inFlight = false;
    // One serial refresh loop covers both stop recovery and terminal hand-off.
    // Never clear the live answer until durable history contains that answer.
    async function refresh() {
      if (disposed || inFlight) return;
      if (watchExternal && document.visibilityState === "hidden") {
        timer = window.setTimeout(() => void refresh(), 3000);
        return;
      }
      inFlight = true;
      let accepted = false;
      try {
        await history.loadSnapshot(repository => {
          const live = liveResponseStore.getSnapshot();
          if (disposed || thread.getState().isRunning || live.runId !== expectedRun ||
              !canHandoffResponse(repository, live)) return false;
          const merged = retainEarlierTurns(repository, thread.getState().messages);
          const signature = JSON.stringify(merged);
          if (signature !== lastImported || live.runId) {
            flushSync(() => {
              if (watchExternal) {
                // AG-UI owns the source messages. Updating that source first
                // keeps the message clients and rendered list in one commit;
                // import() notifies the legacy repository before the source.
                thread.reset(merged.messages.map(entry => entry.message));
              } else {
                thread.import(merged);
              }
              liveResponseStore.clear(threadId);
              expectedRun = undefined;
            });
            lastImported = signature;
          }
          accepted = true;
          return true;
        });
      } catch (error) {
        if (!disposed) console.error("[Harness Console] Failed to refresh durable history", error);
      } finally {
        inFlight = false;
      }
      if (disposed) return;
      onSettled?.();
      // Cancel can finish locally before the server commits its last events.
      if (!accepted || needsRecovery || watchExternal) {
        timer = window.setTimeout(() => void refresh(), !accepted || needsRecovery ? 1500 : 3000);
      }
    }
    const onFocus = () => {
      if (watchExternal && !disposed && !inFlight) {
        window.clearTimeout(timer);
        timer = window.setTimeout(() => void refresh(), 16);
      }
    };
    if (watchExternal) {
      window.addEventListener("focus", onFocus);
      document.addEventListener("visibilitychange", onFocus);
    }
    timer = window.setTimeout(() => void refresh(), 16);
    return () => {
      disposed = true; window.clearTimeout(timer);
      window.removeEventListener("focus", onFocus);
      document.removeEventListener("visibilitychange", onFocus);
    };
  }, [history, revision, running, needsRecovery, thread, threadId, watchExternal]);

  return null;
}
