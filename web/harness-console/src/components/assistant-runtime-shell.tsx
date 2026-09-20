"use client";

import {
  AssistantRuntimeProvider,
} from "@assistant-ui/react";
import { useAgUiRuntime } from "@assistant-ui/react-ag-ui";
import type { ReactNode } from "react";
import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
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
import { DurableHistorySync } from "./durable-history-sync";
import { ThreadHistoryReadyProvider } from "./thread-history-ready";
import { TaskModelProvider } from "./task-model-context";


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
  const [historyReady, setHistoryReady] = useState(false);
  const [knowledgeReferences, setKnowledgeReferences] = useState<string[]>([]);
  const [knowledgeMode, setKnowledgeMode] = useState<"rag" | "wiki">("rag");
  const [loadedKnowledgeKey, setLoadedKnowledgeKey] = useState<string | null>(null);
  const knowledgeStorageKey = `harness:thread-knowledge:${threadId}`;
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
      knowledgeMode,
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
    knowledgeMode,
    refreshDurableHistory,
    spaceId,
    threadId,
  ]);
  const attachments = useMemo(() => createInputAttachmentAdapter(), []);
  // The adapter carries only the thread. Rebuilding it for the agent's own
  // options (a model route or knowledge selection that resolves a moment after
  // opening a task) made DurableHistorySync re-import the snapshot and repaint
  // the whole conversation.
  const agentRef = useRef(agent);
  agentRef.current = agent;
  const history = useMemo(
    () =>
      createThreadHistoryAdapter(threadId, {
        onActiveRun: (serverRunId) =>
          agentRef.current.adoptActiveRun(threadId, serverRunId),
      }),
    [threadId],
  );
  // The history adapter is keyed by threadId only: disposing it whenever the
  // agent binding re-resolves (catalog loads after mount for deep-linked or
  // delivered threads) would abort the first history load and leave the
  // conversation permanently empty.
  useEffect(() => () => history.dispose(), [history]);
  useEffect(
    () => () => {
      void agent.detachActiveRun();
    },
    [agent],
  );
  useEffect(() => {
    try {
      const raw = localStorage.getItem(knowledgeStorageKey);
      const saved = raw ? (JSON.parse(raw) as { references?: string[]; mode?: string }) : null;
      setKnowledgeReferences(Array.isArray(saved?.references) ? [...new Set(saved.references.filter((ref) => typeof ref === "string" && /^[a-z][a-z0-9-]{0,127}$/.test(ref)))] : []);
      setKnowledgeMode(saved?.mode === "wiki" ? "wiki" : "rag");
    } catch {
      setKnowledgeReferences([]);
      setKnowledgeMode("rag");
    }
    setLoadedKnowledgeKey(knowledgeStorageKey);
  }, [knowledgeStorageKey]);

  useEffect(() => {
    try {
      if (loadedKnowledgeKey !== knowledgeStorageKey) return;
      localStorage.setItem(
        knowledgeStorageKey,
        JSON.stringify({ references: knowledgeReferences, mode: knowledgeMode }),
      );
    } catch {
      /* storage unavailable: keep the in-memory selection */
    }
  }, [knowledgeMode, knowledgeReferences, knowledgeStorageKey, loadedKnowledgeKey]);

  useLayoutEffect(() => {
    activateRuntimeThread(threadId);
    activityStore.clear();
    liveResponseStore.clear();
    runStreamStore.clear();
    runReuseStore.clear();
    uploadFeedbackStore.clear();
    // The new thread is empty until its snapshot lands; keep the welcome away.
    setHistoryReady(false);
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
      <ThreadHistoryReadyProvider value={historyReady}>
      <DurableHistorySync threadId={threadId} revision={historyRevision} history={history} onSettled={() => setHistoryReady(true)} />
      <TaskModelProvider
        routes={modelRoutes}
        agentDefaultRouteId={agentDefaultModelRoute}
        overrideRouteId={modelRouteOverride}
        onOverrideChange={onModelRouteOverrideChange}
      >
        <TaskKnowledgeProvider
          selected={knowledgeReferences}
          onChange={setKnowledgeReferences}
          mode={knowledgeMode}
          onModeChange={setKnowledgeMode}
        >
          <div
            className="assistant-runtime-shell"
            data-run-phase={runView?.phase ?? "idle"}
          >
            {children}
          </div>
        </TaskKnowledgeProvider>
      </TaskModelProvider>
      </ThreadHistoryReadyProvider>
    </AssistantRuntimeProvider>
  );
}
