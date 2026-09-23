"use client";
import { createContext, useContext, type ReactNode } from "react";
import type { RunActivity } from "./activity-schema";
import type { RunViewModel } from "./run-view-model";
import type { RunStreamSnapshot } from "./run-stream-store";
import type { LiveResponseSnapshot } from "./live-response-store";
import type { PendingApprovalSnapshot } from "./approval-store";
/** A Playground uses the task conversation UI with its own run state, never the active task's globals. */
export interface ConversationScope {
  compactComposer?: boolean;
  composerPlaceholder?: string;
  composerAccessory?: ReactNode;
  activity?: RunActivity;
  view?: RunViewModel;
  stream: RunStreamSnapshot;
  live: LiveResponseSnapshot;
  approval: PendingApprovalSnapshot;
  onOpenFiles: () => void;
  onNew: () => void;
  onConfigureKnowledge: () => void;
  onApproval: (id: string, decision: "approved" | "rejected") => Promise<void>;
  afterMessage: (messageId: string) => ReactNode;
}
const Scope = createContext<ConversationScope | null>(null);
export const ConversationScopeProvider = Scope.Provider;
export function useConversationScope() {
  return useContext(Scope);
}
