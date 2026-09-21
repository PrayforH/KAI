"use client";
import { useConversationScope } from "./conversation-scope";

import { useSyncExternalStore } from "react";
import { isActiveRuntimeThread } from "./runtime-thread-scope";

export type RunStreamStatus = "idle" | "running" | "complete" | "error";

export interface RunStreamSnapshot {
  threadId?: string;
  runId?: string;
  status: RunStreamStatus;
}

const emptySnapshot: RunStreamSnapshot = Object.freeze({
  status: "idle",
});

let snapshot = emptySnapshot;
const listeners = new Set<() => void>();

function publish(next: RunStreamSnapshot) {
  if (
    snapshot.threadId === next.threadId &&
    snapshot.runId === next.runId &&
    snapshot.status === next.status
  ) return;
  snapshot = next;
  for (const listener of listeners) listener();
}

function settle(
  runId: string | undefined,
  status: "complete" | "error",
  threadId?: string,
) {
  if (!isActiveRuntimeThread(threadId)) return;
  if (runId && snapshot.runId && snapshot.runId !== runId) return;
  publish({
    threadId: threadId ?? snapshot.threadId,
    runId: runId ?? snapshot.runId,
    status,
  });
}

export const runStreamStore = {
  clear(threadId?: string) {
    if (!isActiveRuntimeThread(threadId)) return;
    publish(emptySnapshot);
  },
  startRun(runId: string, threadId?: string) {
    if (!isActiveRuntimeThread(threadId)) return;
    publish({ threadId, runId, status: "running" });
  },
  completeRun(runId?: string, threadId?: string) {
    settle(runId, "complete", threadId);
  },
  failRun(runId?: string, threadId?: string) {
    settle(runId, "error", threadId);
  },
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  getSnapshot() {
    return snapshot;
  },
};

export function useRunStream(): RunStreamSnapshot {
  const scope = useConversationScope();
  const globalSnapshot = useSyncExternalStore(
    runStreamStore.subscribe,
    runStreamStore.getSnapshot,
    () => emptySnapshot,
  );
  return scope ? scope.stream : globalSnapshot;
}
