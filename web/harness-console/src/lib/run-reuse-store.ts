"use client";
import { useConversationScope } from "./conversation-scope";

import { useSyncExternalStore } from "react";

export interface RunReuseNotice {
  runId: string;
  canonicalClientRunId: string;
}

const emptySnapshot: RunReuseNotice | null = null;
let snapshot: RunReuseNotice | null = emptySnapshot;
const listeners = new Set<() => void>();

function publish(next: RunReuseNotice | null) {
  snapshot = next;
  for (const listener of listeners) listener();
}

export const runReuseStore = {
  show(notice: RunReuseNotice) {
    publish(notice);
  },
  clear() {
    if (snapshot !== null) publish(null);
  },
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  getSnapshot() {
    return snapshot;
  },
};

export function useRunReuseNotice(): RunReuseNotice | null {
  const scope = useConversationScope();
  const globalSnapshot = useSyncExternalStore(
    runReuseStore.subscribe,
    runReuseStore.getSnapshot,
    () => emptySnapshot,
  );
  return scope ? null : globalSnapshot;
}
