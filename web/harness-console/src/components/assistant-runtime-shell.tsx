"use client";

import {
  AssistantRuntimeProvider,
  useThreadRuntime,
  useAuiState,
} from "@assistant-ui/react";
import { useAgUiRuntime } from "@assistant-ui/react-ag-ui";
import type { ReactNode } from "react";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useState,
} from "react";
import { activityStore, useRunViewModel } from "../lib/activity-store";
import { HarnessHttpAgent } from "../lib/harness-agent";
import { createInputAttachmentAdapter } from "../lib/input-attachment-adapter";
import { liveResponseStore } from "../lib/live-response-store";
import { runStreamStore } from "../lib/run-stream-store";
import { runReuseStore } from "../lib/run-reuse-store";
import { uploadFeedbackStore } from "../lib/upload-feedback-store";
import {
  createThreadHistoryAdapter,
  invalidateThreadHistory,
} from "../lib/task-history";
import { activateRuntimeThread } from "../lib/runtime-thread-scope";
import type { TaskModelRoute } from "../lib/task-model-catalog";
import { TaskKnowledgeProvider } from "./task-knowledge-context";
import { TaskModelProvider } from "./task-model-context";

function DurableHistorySync({
  revision,
  history,
}: {
  revision: number;
  history: ReturnType<typeof createThreadHistoryAdapter>;
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
          if (!disposed && !thread.getState().isRunning) thread.import(repository);
        });
      } catch (error) {
        if (!disposed) console.error("[Harness Console] Failed to recover active task", error);
      } finally { pending = false; }
    }
    const timer = window.setInterval(() => void refresh(), 1500);
    void refresh();
    return () => { disposed = true; window.clearInterval(timer); };
  }, [history, needsRecovery, thread]);


  useEffect(() => {
    let disposed = false;
    const timer = window.setTimeout(() => {
      void history
        .loadSnapshot((repository) => {
          if (!disposed && repository && !thread.getState().isRunning) {
            thread.import(repository);
          }
        })
        .catch((error: unknown) => {
          if (!disposed) {
            console.error(
              "[Harness Console] Failed to refresh durable history",
              error,
            );
          }
        });
    }, 120);
    return () => {
      disposed = true;
      window.clearTimeout(timer);
    };
  }, [history, revision, thread]);

  return null;
}

export function AssistantRuntimeShell({
  threadId,
  agentName,
  agentVersion,
  agentOwnerUserId,
  spaceId,
  agentDefaultModelRoute,
  modelRoutes,
  modelRouteOverride,
  onModelRouteOverrideChange,
  children,
}: {
  threadId: string;
  agentName: string;
  agentVersion: string;
  agentOwnerUserId?: string;
  spaceId?: string;
  agentDefaultModelRoute: string | null;
  modelRoutes: TaskModelRoute[];
  modelRouteOverride: string | null;
  onModelRouteOverrideChange: (routeId: string | null) => void;
  children: ReactNode;
}) {
  const runView = useRunViewModel();
  const [historyRevision, setHistoryRevision] = useState(0);
  const [knowledgeReferences, setKnowledgeReferences] = useState<string[]>([]);
  const conversationalModelRouteOverride = modelRoutes.find(
    (route) => route.id === modelRouteOverride && route.modelType !== "video_generation",
  )?.id ?? null;
  const refreshDurableHistory = useCallback(() => {
    invalidateThreadHistory(threadId);
    setHistoryRevision((current) => current + 1);
  }, [threadId]);
  const agent = useMemo(() => {
    const query = new URLSearchParams({
      agent_name: agentName,
      agent_version: agentVersion,
    });
    if (agentOwnerUserId) query.set("agent_owner_user_id", agentOwnerUserId);
    if (spaceId) query.set("space_id", spaceId);
    const next = new HarnessHttpAgent({
      url: `/api/agui?${query.toString()}`,
      modelRouteOverride: conversationalModelRouteOverride,
      knowledgeReferences,
      onRunSucceeded: refreshDurableHistory,
    });
    next.threadId = threadId;
    return next;
  }, [
    agentName,
    agentOwnerUserId,
    agentVersion,
    conversationalModelRouteOverride,
    knowledgeReferences,
    refreshDurableHistory,
    spaceId,
    threadId,
  ]);
  const attachments = useMemo(() => createInputAttachmentAdapter(), []);
  const history = useMemo(
    () =>
      createThreadHistoryAdapter(threadId, {
        onActiveRun: (serverRunId) =>
          agent.adoptActiveRun(threadId, serverRunId),
      }),
    [agent, threadId],
  );
  useEffect(
    () => () => {
      history.dispose();
      void agent.detachActiveRun();
    },
    [agent, history],
  );
  useLayoutEffect(() => {
    setKnowledgeReferences([]);
    activateRuntimeThread(threadId);
    activityStore.clear();
    liveResponseStore.clear();
    runStreamStore.clear();
    runReuseStore.clear();
    uploadFeedbackStore.clear();
  }, [threadId]);
  const runtime = useAgUiRuntime({
    agent,
    showThinking: true,
    adapters: { attachments, history },
    onCancel: () => agent.cancelActiveRun(),
    onError: (error) => console.error("[Harness Console]", error),
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <DurableHistorySync revision={historyRevision} history={history} />
      <TaskModelProvider
        routes={modelRoutes}
        agentDefaultRouteId={agentDefaultModelRoute}
        overrideRouteId={modelRouteOverride}
        onOverrideChange={onModelRouteOverrideChange}
      >
        <TaskKnowledgeProvider
          selected={knowledgeReferences}
          onChange={setKnowledgeReferences}
        >
          <div
            className="assistant-runtime-shell"
            data-run-phase={runView?.phase ?? "idle"}
          >
            {children}
          </div>
        </TaskKnowledgeProvider>
      </TaskModelProvider>
    </AssistantRuntimeProvider>
  );
}
