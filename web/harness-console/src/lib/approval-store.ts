"use client";
import { useConversationScope } from "./conversation-scope";

import { useSyncExternalStore } from "react";
import type { ApprovalDetails } from "../components/approval-card";
import { isActiveRuntimeThread } from "./runtime-thread-scope";

export interface PendingApprovalSnapshot {
  details?: ApprovalDetails;
  visible: boolean;
}

const emptySnapshot: PendingApprovalSnapshot = Object.freeze({
  visible: false,
});

let snapshot = emptySnapshot;
const settledApprovalIds = new Set<string>();
const listeners = new Set<() => void>();

function publish(next: PendingApprovalSnapshot) {
  snapshot = next;
  for (const listener of listeners) listener();
}

export const approvalStore = {
  show(details: ApprovalDetails, threadId?: string) {
    if (!isActiveRuntimeThread(threadId)) return;
    if (settledApprovalIds.has(details.approval_id)) return;
    if (
      snapshot.visible &&
      snapshot.details?.approval_id === details.approval_id
    ) {
      return;
    }
    publish({ details, visible: true });
  },
  settle(approvalId: string, threadId?: string) {
    if (!isActiveRuntimeThread(threadId)) return;
    settledApprovalIds.add(approvalId);
    this.clear(approvalId, threadId);
  },
  clear(approvalId?: string, threadId?: string) {
    if (!isActiveRuntimeThread(threadId)) return;
    if (
      snapshot === emptySnapshot ||
      (approvalId && snapshot.details?.approval_id !== approvalId)
    ) {
      return;
    }
    publish(emptySnapshot);
  },
  reset(threadId?: string) {
    if (!isActiveRuntimeThread(threadId)) return;
    settledApprovalIds.clear();
    this.clear(undefined, threadId);
  },
  subscribe(listener: () => void) {
    listeners.add(listener);
    return () => listeners.delete(listener);
  },
  getSnapshot() {
    return snapshot;
  },
};

export function usePendingApproval(): PendingApprovalSnapshot {
  const scope = useConversationScope();
  const globalSnapshot = useSyncExternalStore(
    approvalStore.subscribe,
    approvalStore.getSnapshot,
    () => emptySnapshot,
  );
  return scope ? scope.approval : globalSnapshot;
}
