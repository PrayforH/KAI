import { createUserScopedStorage } from "./thread-store";
import { loadTaskModelOverride, saveTaskModelOverride } from "./task-model-catalog";
import {
  ExportedMessageRepository,
  type ChatModelRunOptions,
  type ChatModelRunResult,
  type ThreadHistoryAdapter,
} from "@assistant-ui/core";
import { fromAgUiMessages } from "@assistant-ui/react-ag-ui";
import { useCallback, useEffect, useRef, useState, type RefObject } from "react";
import type { ApprovalDetails } from "../components/approval-card";
import { latestHistoryRunActivity, type RunActivity } from "./activity-schema";
import { activityStore } from "./activity-store";
import { approvalStore } from "./approval-store";
import { requireAuthenticatedResponse } from "./client-auth";
import { clearHistoryIndex } from "./history-index";
import { setThreadReplyReferences } from "./reply-references";

export interface TaskSummary {
  thread_id: string;
  session_id: string;
  title: string;
  agent_name: string;
  agent_version: string;
  agent_owner_user_id: string;
  space_id?: string | null;
  status: string;
  run_id?: string;
  created_at: string;
  updated_at: string;
  archived_at?: string | null;
  pinned_at?: string | null;
  last_read_at?: string | null;
  project_id?: string | null;
  pending_approval?: (ApprovalDetails & { status: string }) | null;
  pending_input?: boolean;
}

const taskListRequests = new Map<boolean, Promise<TaskSummary[]>>();
const taskListSnapshots = new Map<boolean, { receivedAt: number; tasks: TaskSummary[] }>();
const taskListCoalesceMs = 250;
export const TASK_LIST_REQUEST_TIMEOUT_MS = 8_000;
export const THREAD_HISTORY_PREFETCH_TTL_MS = 30_000;
const threadHistoryCacheMax = 8;
const THREAD_HISTORY_PAGE_RUNS = 10;
/** Earlier pages trade payload for far fewer round trips while paging back;
 * the latest page keeps its small size so thread open stays fast. */
export const THREAD_HISTORY_EARLIER_PAGE_RUNS = 30;
const threadHistoryAccumulatedMax = 8;

export interface ThreadHistoryResponse {
  thread_id: string;
  status: string;
  run_id?: string | null;
  pending_approval?: ApprovalDetails;
  messages: Array<{
    id: string;
    role: string;
    content: string;
    toolCalls?: unknown[];
    tool_calls?: unknown[];
    toolCallId?: string;
    tool_call_id?: string;
  }>;
  replies?: Record<string, { messageId?: string; excerpt?: string | null }>;
  next_cursor?: string | null;
  has_more?: boolean;
  total?: number;
}

const activeStatuses = new Set([
  "queued",
  "provisioning",
  "running",
  "waiting_approval",
  "cancelling",
]);

const threadHistoryRequests = new Map<string, Promise<ThreadHistoryResponse | null>>();
const historyValidators = new Map<string, { etag: string; history: ThreadHistoryResponse }>();
const threadHistorySnapshots = new Map<
  string,
  { receivedAt: number; history: ThreadHistoryResponse | null }
>();

function cacheThreadHistory(threadId: string, history: ThreadHistoryResponse | null) {
  threadHistorySnapshots.delete(threadId);
  threadHistorySnapshots.set(threadId, { receivedAt: Date.now(), history });
  while (threadHistorySnapshots.size > threadHistoryCacheMax) {
    const oldest = threadHistorySnapshots.keys().next().value;
    if (typeof oldest !== "string") break;
    threadHistorySnapshots.delete(oldest);
  }
}

function resumedStatus(status: string): NonNullable<ChatModelRunResult["status"]> {
  if (activeStatuses.has(status)) return { type: "running" };
  if (status === "cancelled") return { type: "incomplete", reason: "cancelled" };
  if (["failed", "rejected", "timed_out"].includes(status)) {
    return {
      type: "incomplete",
      reason: "error",
      error: `任务已${status === "timed_out" ? "超时" : "失败"}`,
    };
  }
  return { type: "complete", reason: "unknown" };
}

function waitForHistoryPoll(signal: AbortSignal, milliseconds = 500) {
  return new Promise<void>((resolve, reject) => {
    if (signal.aborted) {
      reject(signal.reason);
      return;
    }
    const timer = globalThis.setTimeout(done, milliseconds);
    function done() {
      signal.removeEventListener("abort", cancelled);
      resolve();
    }
    function cancelled() {
      globalThis.clearTimeout(timer);
      reject(signal.reason);
    }
    signal.addEventListener("abort", cancelled, { once: true });
  });
}

function historyUrl(threadId: string, before?: string) {
  const base = `/api/agui/threads/${encodeURIComponent(threadId)}/history`;
  const query = new URLSearchParams({
    limit: String(before ? THREAD_HISTORY_EARLIER_PAGE_RUNS : THREAD_HISTORY_PAGE_RUNS),
  });
  if (before) query.set("before", before);
  return `${base}?${query.toString()}`;
}

async function loadThreadHistory(
  threadId: string,
  signal?: AbortSignal,
  before?: string,
): Promise<ThreadHistoryResponse | null> {
  const isLatestPage = before === undefined;
  if (isLatestPage) {
    const snapshot = threadHistorySnapshots.get(threadId);
    if (snapshot && Date.now() - snapshot.receivedAt < THREAD_HISTORY_PREFETCH_TTL_MS) {
      threadHistorySnapshots.delete(threadId);
      threadHistorySnapshots.set(threadId, snapshot);
      return snapshot.history;
    }
    if (snapshot) threadHistorySnapshots.delete(threadId);
  }

  function createRequest(): Promise<ThreadHistoryResponse | null> {
    const validator = isLatestPage ? historyValidators.get(threadId) : undefined;
    return fetch(historyUrl(threadId, before), { cache: "no-store",
      ...(validator ? { headers: { "If-None-Match": validator.etag } } : {}),
    })
      .then(requireAuthenticatedResponse)
      .then(async (response) => {
        if (response.status === 304 && validator) return validator.history;
        if (response.status === 404) return null;
        if (!response.ok) {
          throw new Error((await response.text()) || `HTTP ${response.status}`);
        }
        const history = await response.json() as ThreadHistoryResponse;
        if (isLatestPage) {
          historyValidators.delete(threadId);
          const etag = response.headers.get("etag");
          if (etag && !activeStatuses.has(history.status)) historyValidators.set(threadId, { etag, history });
          while (historyValidators.size > threadHistoryCacheMax) historyValidators.delete(historyValidators.keys().next().value!);
        }
        return history;
      })
      .then((history) => {
        if (isLatestPage && threadHistoryRequests.get(threadId) === request) {
          if (!history || !activeStatuses.has(history.status)) cacheThreadHistory(threadId, history);
          seedAccumulatedHistory(threadId, history);
          setThreadReplyReferences(threadId, history?.replies);
        }
        return history;
      })
      .finally(() => {
        if (threadHistoryRequests.get(threadId) === request) {
          threadHistoryRequests.delete(threadId);
        }
      });
  }

  // Only the latest page is deduplicated; earlier pages are on-demand fetches
  // issued once per click and must never be reused as the page-1 request.
  let request: Promise<ThreadHistoryResponse | null>;
  if (isLatestPage) {
    request = threadHistoryRequests.get(threadId) ?? createRequest();
    threadHistoryRequests.set(threadId, request);
  } else {
    request = createRequest();
  }

  if (!signal) return request;
  const abortSignal = signal;
  if (abortSignal.aborted) throw abortSignal.reason;
  return new Promise<ThreadHistoryResponse | null>((resolve, reject) => {
    function cancelled() {
      reject(abortSignal.reason);
    }
    abortSignal.addEventListener("abort", cancelled, { once: true });
    request.then(resolve, reject).finally(() => {
      abortSignal.removeEventListener("abort", cancelled);
    });
  });
}

export function prefetchThreadHistory(threadId: string): Promise<void> {
  return loadThreadHistory(threadId).then(() => undefined);
}

/**
 * Full history for the trace console: page through every visible run with the
 * server's max page size instead of the conversation's 10-run page. Returns
 * plain messages (not imported into the chat runtime).
 */
export const TRACE_HISTORY_PAGE_RUNS = 100;
export const TRACE_HISTORY_MAX_PAGES = 20;

export async function loadFullThreadHistory(
  threadId: string,
  options: {
    onNotFound?: () => void;
    /** Lets a reader paint each page instead of waiting for the last one. */
    onPage?: (page: ThreadHistoryResponse, index: number) => void;
    signal?: AbortSignal;
  } = {},
): Promise<ThreadHistoryResponse["messages"]> {
  const messages: ThreadHistoryResponse["messages"] = [];
  let before: string | undefined;
  for (let page = 0; page < TRACE_HISTORY_MAX_PAGES; page += 1) {
    const query = new URLSearchParams({ limit: String(TRACE_HISTORY_PAGE_RUNS) });
    if (before) query.set("before", before);
    const response = requireAuthenticatedResponse(
      await fetch(
        `/api/agui/threads/${encodeURIComponent(threadId)}/history?${query.toString()}`,
        { cache: "no-store", signal: options.signal },
      ),
    );
    if (response.status === 404) {
      // The thread binding is not durable yet (first run still being
      // accepted); the caller decides how to wait for it.
      options.onNotFound?.();
      return messages;
    }
    if (!response.ok) {
      throw new Error((await response.text()) || `HTTP ${response.status}`);
    }
    const history = (await response.json()) as ThreadHistoryResponse;
    messages.push(...history.messages);
    options.onPage?.(history, page);
    if (!history.has_more || !history.next_cursor) break;
    before = history.next_cursor;
  }
  return messages;
}

export function invalidateThreadHistory(threadId: string, options: { keepPages?: boolean } = {}): void {
  threadHistoryRequests.delete(threadId);
  threadHistorySnapshots.delete(threadId);
  historyValidators.delete(threadId);
  // The drawer's cached index is derived from this history; a new turn or a
  // branch switch must not keep showing the previous rows.
  clearHistoryIndex(threadId);
  if (!options.keepPages) resetAccumulatedHistory(threadId);
}

export interface AccumulatedThreadHistory {
  messages: ThreadHistoryResponse["messages"];
  nextCursor: string | null;
  hasMore: boolean;
  /** Every visible run of the thread, not only the materialised page. */
  total: number;
}

interface AccumulationEntry extends AccumulatedThreadHistory {
  loading: boolean;
  expanded?: boolean;
}

/**
 * Pagination outcome. `busy` means another reader already has a page in
 * flight, so the caller must wait — it is not the end of the thread.
 */
export type LoadEarlierResult = "loaded" | "busy" | "exhausted" | "failed";
/** Boolean is the legacy shape; true/false still read as loaded/exhausted. */
export type LoadEarlierFn = (pages?: number) => Promise<LoadEarlierResult | boolean>;

/** Fresh page copies win, without duplicating messages at a moving page boundary. */
function mergeHistoryMessages(earlier: ThreadHistoryResponse["messages"], latest: ThreadHistoryResponse["messages"]) {
  const current = new Set(latest.map(message => message.id));
  return [...earlier.filter(message => !current.has(message.id)), ...latest];
}

const accumulatedHistory = new Map<string, AccumulationEntry>();
const accumulationListeners = new Map<string, Set<() => void>>();
/** In-flight page requests, so locate can wait instead of declaring "missing". */
const earlierLoads = new Map<string, Promise<LoadEarlierResult>>();

/** Resolves when any in-flight earlier-page request for the thread settles. */
export function waitForThreadHistoryLoad(threadId: string, timeoutMs = 2_000): Promise<void> {
  const pending = earlierLoads.get(threadId);
  if (!pending) return Promise.resolve();
  return Promise.race([
    pending.then(() => undefined, () => undefined),
    new Promise<void>(resolve => globalThis.setTimeout(resolve, timeoutMs)),
  ]);
}

function publishAccumulatedHistory(threadId: string): void {
  accumulationListeners.get(threadId)?.forEach((listener) => listener());
}

function resetAccumulatedHistory(threadId: string): void {
  const entry = accumulatedHistory.get(threadId);
  if (!entry) return;
  accumulatedHistory.delete(threadId);
  publishAccumulatedHistory(threadId);
}

function seedAccumulatedHistory(
  threadId: string,
  history: ThreadHistoryResponse | null,
): void {
  if (!history) {
    resetAccumulatedHistory(threadId);
    return;
  }
  const previous = accumulatedHistory.get(threadId);
  accumulatedHistory.delete(threadId);
  accumulatedHistory.set(threadId, {
    messages: previous?.expanded ? mergeHistoryMessages(previous.messages, history.messages) : history.messages,
    nextCursor: previous?.expanded ? previous.nextCursor : history.next_cursor ?? null,
    hasMore: previous?.expanded ? previous.hasMore : history.has_more ?? false,
    total: history.total ?? history.messages.length,
    loading: previous?.loading ?? false,
    expanded: previous?.expanded,
  });
  while (accumulatedHistory.size > threadHistoryAccumulatedMax) {
    const oldest = accumulatedHistory.keys().next().value;
    if (typeof oldest !== "string") break;
    accumulatedHistory.delete(oldest);
  }
  publishAccumulatedHistory(threadId);
}

export function useThreadHistoryPagination(
  threadId: string,
  options: {
    importRepository: (
      repository: ReturnType<typeof ExportedMessageRepository.fromArray>,
    ) => void;
  },
): { hasMore: boolean; loading: boolean; total: number; loadEarlier: LoadEarlierFn; waitForLoading: () => Promise<void> } {
  const [snapshot, setSnapshot] = useState<AccumulationEntry | null>(() =>
    accumulatedHistory.get(threadId) ?? null,
  );
  const importRepositoryRef = useRef(options.importRepository);
  importRepositoryRef.current = options.importRepository;
  const activeThread = useRef<string | null>(threadId);

  useEffect(() => {
    activeThread.current = threadId;
    setSnapshot(accumulatedHistory.get(threadId) ?? null);
    const listener = () => setSnapshot(accumulatedHistory.get(threadId) ?? null);
    const listeners = accumulationListeners.get(threadId) ?? new Set<() => void>();
    listeners.add(listener);
    accumulationListeners.set(threadId, listeners);
    return () => {
      activeThread.current = null;
      listeners.delete(listener);
      if (listeners.size === 0) accumulationListeners.delete(threadId);
    };
  }, [threadId]);

  const loadEarlier = useCallback(async (pages = 1): Promise<LoadEarlierResult> => {
    const entry = accumulatedHistory.get(threadId);
    // "Busy" and "nothing older" used to share `false`, which made a locate
    // jump report a missing message while a page was simply still in flight.
    if (!entry || !entry.nextCursor) return "exhausted";
    if (entry.loading) return "busy";
    const pending = { ...entry, loading: true };
    accumulatedHistory.set(threadId, pending);
    publishAccumulatedHistory(threadId);
    // A locate jump can need several pages. Fetching them in one gesture means
    // one merge and one repository import instead of re-rendering the whole
    // imported thread once per page.
    let cursor: string | null = entry.nextCursor;
    let hasMore = entry.hasMore;
    let earlier: ThreadHistoryResponse["messages"] = [];
    const request = (async (): Promise<LoadEarlierResult> => {
      try {
        for (let index = 0; index < Math.max(1, pages) && cursor; index += 1) {
          const page = await loadThreadHistory(threadId, undefined, cursor);
          if (!page) throw new Error("Earlier history is unavailable");
          earlier = mergeHistoryMessages(page.messages, earlier);
          hasMore = page.has_more ?? false;
          cursor = hasMore ? page.next_cursor ?? null : null;
          if (!hasMore) break;
        }
        // Another reader may have seeded newer messages while these pages were in
        // flight: prepend to the current list rather than to the pre-request
        // snapshot, or those messages are dropped and a stale list is imported.
        const latest = accumulatedHistory.get(threadId) ?? entry;
        accumulatedHistory.set(threadId, {
          messages: mergeHistoryMessages(earlier, latest.messages),
          nextCursor: cursor,
          hasMore,
          total: latest.total,
          loading: false,
          expanded: true,
        });
        publishAccumulatedHistory(threadId);
        if (activeThread.current !== threadId) return "loaded";
        importRepositoryRef.current(
          ExportedMessageRepository.fromArray(
            fromAgUiMessages(accumulatedHistory.get(threadId)?.messages ?? [], {
              showThinking: true,
            }),
          ),
        );
        return "loaded";
      } catch (error) {
        accumulatedHistory.set(threadId, {
          ...(accumulatedHistory.get(threadId) ?? entry),
          loading: false,
        });
        publishAccumulatedHistory(threadId);
        console.error("[Harness Console] Failed to load earlier messages", error);
        return "failed";
      }
    })();
    earlierLoads.set(threadId, request);
    try {
      return await request;
    } finally {
      if (earlierLoads.get(threadId) === request) earlierLoads.delete(threadId);
    }
  }, [threadId]);

  return {
    hasMore: snapshot?.hasMore ?? false,
    loading: snapshot?.loading ?? false,
    total: snapshot?.total ?? 0,
    loadEarlier,
    waitForLoading: useCallback(() => waitForThreadHistoryLoad(threadId), [threadId]),
  };
}

/**
 * A transcript stores the activity it saw while streaming, so a run that ended
 * right after the last write still says "running". The thread's own latest-run
 * status is authoritative: settling it here keeps a refresh from replaying a
 * live status row over a turn that already finished.
 */
export function settleHistoryActivity(
  activity: RunActivity,
  threadStatus: string,
): RunActivity {
  if (activeStatuses.has(threadStatus) || !activeStatuses.has(activity.status)) return activity;
  return { ...activity, status: threadStatus };
}

function publishHistoryState(history: ThreadHistoryResponse, threadId: string) {
  if (history.pending_approval && history.pending_approval.run_id === history.run_id) {
    approvalStore.show(history.pending_approval, threadId);
  }
  const restored = latestHistoryRunActivity(history.messages);
  if (!restored) return;
  const restoredActivity = settleHistoryActivity(restored, history.status);
  if (JSON.stringify(restoredActivity) !== JSON.stringify(activityStore.getSnapshot())) {
    activityStore.publish(restoredActivity, threadId, { replaceRun: true });
  }
}

async function json<T>(url: string, timeoutMs?: number): Promise<T> {
  const controller = new AbortController();
  const timeout = timeoutMs === undefined
    ? undefined
    : globalThis.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = requireAuthenticatedResponse(
      await fetch(url, { cache: "no-store", signal: controller.signal }),
    );
    if (!response.ok) {
      throw new Error((await response.text()) || `HTTP ${response.status}`);
    }
    return response.json() as Promise<T>;
  } catch (error) {
    if (controller.signal.aborted) {
      throw new Error("任务列表读取超时，请重试", { cause: error });
    }
    throw error;
  } finally {
    if (timeout !== undefined) globalThis.clearTimeout(timeout);
  }
}

export function loadTasks(archived = false): Promise<TaskSummary[]> {
  const inFlight = taskListRequests.get(archived);
  if (inFlight) return inFlight;
  const snapshot = taskListSnapshots.get(archived);
  if (snapshot && Date.now() - snapshot.receivedAt < taskListCoalesceMs) {
    return Promise.resolve(snapshot.tasks);
  }
  const request = json<TaskSummary[]>(
    `/api/agui/threads?archived=${archived}`,
    TASK_LIST_REQUEST_TIMEOUT_MS,
  )
    .then((tasks) => {
      if (taskListRequests.get(archived) === request) {
        taskListSnapshots.set(archived, { receivedAt: Date.now(), tasks });
      }
      return tasks;
    })
    .finally(() => {
      if (taskListRequests.get(archived) === request) {
        taskListRequests.delete(archived);
      }
    });
  taskListRequests.set(archived, request);
  return request;
}

export function searchTasks(query: string): Promise<TaskSummary[]> {
  const parameters = new URLSearchParams({ q: query, limit: "12" });
  return json<TaskSummary[]>(`/api/agui/threads?${parameters}`, TASK_LIST_REQUEST_TIMEOUT_MS);
}

/**
 * Last fetched task list without a network round trip. Fresh sidebar mounts
 * (page navigations) seed from this so the 任务/智能体 lists render in place
 * instead of flashing the loading state and refreshing every row.
 */
export function peekCachedTasks(archived = false): TaskSummary[] | null {
  return taskListSnapshots.get(archived)?.tasks ?? null;
}

/** Ask every mounted task sidebar to refresh immediately. */
export function notifyTaskListChanged(): void {
  taskListSnapshots.clear();
  taskListSnapshots.clear();
  taskListRequests.clear();
  window.dispatchEvent(new CustomEvent("harness:task-list-changed"));
}

export async function setTaskPinned(
  threadId: string,
  pinned: boolean,
): Promise<void> {
  const response = requireAuthenticatedResponse(
    await fetch(`/api/agui/threads/${encodeURIComponent(threadId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pinned }),
    }),
  );
  if (!response.ok) {
    throw new Error((await response.text()) || `HTTP ${response.status}`);
  }
  taskListSnapshots.clear();
}

export async function setTaskTitle(
  threadId: string,
  title: string,
): Promise<string> {
  const response = requireAuthenticatedResponse(
    await fetch(`/api/agui/threads/${encodeURIComponent(threadId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title }),
    }),
  );
  if (!response.ok) {
    throw new Error((await response.text()) || `HTTP ${response.status}`);
  }
  const result = await response.json() as { title: string | null };
  taskListSnapshots.clear();
  return result.title ?? title;
}

export async function markTaskRead(threadId: string, updatedAt: string): Promise<string> {
  const response = requireAuthenticatedResponse(await fetch(
    `/api/agui/threads/${encodeURIComponent(threadId)}/read`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ updated_at: updatedAt }),
      signal: AbortSignal.timeout(TASK_LIST_REQUEST_TIMEOUT_MS),
    },
  ));
  if (!response.ok) throw new Error((await response.text()) || `HTTP ${response.status}`);
  const result = await response.json() as { last_read_at: string };
  taskListSnapshots.clear();
  return result.last_read_at;
}

export function isTaskRead(task: TaskSummary): boolean {
  return Boolean(task.last_read_at && Date.parse(task.last_read_at) >= Date.parse(task.updated_at));
}

export async function setTaskArchived(
  threadId: string,
  archived: boolean,
): Promise<void> {
  const response = requireAuthenticatedResponse(
    await fetch(`/api/agui/threads/${encodeURIComponent(threadId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ archived }),
    }),
  );
  if (!response.ok) {
    throw new Error((await response.text()) || `HTTP ${response.status}`);
  }
  taskListSnapshots.clear();
}

export function createThreadHistoryAdapter(
  threadId: string,
  options: { onActiveRun?: (serverRunId: string) => void; isLocalOnly?: () => boolean; reuseInitialSnapshot?: boolean } = {},
): ThreadHistoryAdapter & { dispose(): void; loadSnapshot(onLoaded?: (repository: ReturnType<typeof ExportedMessageRepository.fromArray>) => void | boolean): Promise<ReturnType<typeof ExportedMessageRepository.fromArray>> } {
  const disposal = new AbortController();
  let snapshotLoaded = false;
  return {
    async loadSnapshot(onLoaded) {
      if (options.isLocalOnly?.()) {
        const repository = ExportedMessageRepository.fromArray([]);
        onLoaded?.(repository);
        return repository;
      }
      // Revalidate the latest page, but keep an initial load already in flight.
      // Dropping that request made runtime startup and external sync fetch the
      // same history twice. Earlier-page state belongs to the reader as well.
      if (!options.reuseInitialSnapshot || snapshotLoaded) threadHistorySnapshots.delete(threadId);
      snapshotLoaded = true;
      const history = await loadThreadHistory(threadId).catch((error) => {
        if (disposal.signal.aborted) return null;
        throw error;
      });
      const messages = options.reuseInitialSnapshot ? accumulatedHistory.get(threadId)?.messages : undefined;
      const converted = history ? fromAgUiMessages(messages ?? history.messages, { showThinking: true }) : [];
      const repository = ExportedMessageRepository.fromArray(converted);
      // Import terminal text before publishing the phase that stops recovery polling.
      const accepted = !disposal.signal.aborted && onLoaded?.(repository) !== false;
      if (history && accepted) publishHistoryState(history, threadId);
      return repository;
    },
    async load() {
      if (options.isLocalOnly?.()) return ExportedMessageRepository.fromArray([]);
      // Deliberately not bound to the disposal signal: the shell remounts when
      // the agent binding re-resolves, and an abort here would be cached by the
      // runtime core, leaving the delivered conversation permanently empty.
      // The signal only stops the resume polling below.
      const history = await loadThreadHistory(threadId);
      if (!history) {
        return ExportedMessageRepository.fromArray([]);
      }
      publishHistoryState(history, threadId);
      const repository = ExportedMessageRepository.fromArray(
        fromAgUiMessages(options.reuseInitialSnapshot ? accumulatedHistory.get(threadId)?.messages ?? history.messages : history.messages, { showThinking: true }),
      );
      if (!history.run_id || history.run_id.startsWith("video_job_") || !activeStatuses.has(history.status)) {
        return repository;
      }

      options.onActiveRun?.(history.run_id);
      const activeAssistantId = `assistant-${history.run_id}`;
      const activeAssistant = repository.messages.find(
        (item) => item.message.id === activeAssistantId,
      );
      const resumeRepository = ExportedMessageRepository.fromArray(
        repository.messages
          .filter((item) => item.message.id !== activeAssistantId)
          .map((item) => item.message),
      );
      return {
        ...resumeRepository,
        // Do not briefly render the durable partial response and then replace
        // it with assistant-ui's resumed placeholder.  Loading only through
        // its parent keeps one stable visual response slot throughout restore.
        headId: activeAssistant?.parentId ?? resumeRepository.messages.at(-1)?.message.id ?? null,
        unstable_resume: true,
      };
    },
    async *resume(resumeOptions: ChatModelRunOptions) {
      const signal = AbortSignal.any([
        disposal.signal,
        resumeOptions.abortSignal,
      ]);
      let lastSnapshot = "";
      while (!signal.aborted) {
        let history: ThreadHistoryResponse | null;
        try {
          history = await loadThreadHistory(threadId, signal);
        } catch (error) {
          if (signal.aborted) return;
          throw error;
        }
        if (!history) return;
        publishHistoryState(history, threadId);
        if (history.run_id && activeStatuses.has(history.status)) {
          options.onActiveRun?.(history.run_id);
        }

        const repository = ExportedMessageRepository.fromArray(
          fromAgUiMessages(history.messages, { showThinking: true }),
        );
        const assistant = history.run_id
          ? repository.messages.find(
              (item) => item.message.id === `assistant-${history.run_id}`,
            )?.message
          : undefined;
        const status = resumedStatus(history.status);
        const update: ChatModelRunResult = {
          ...(assistant?.role === "assistant"
            ? { content: assistant.content, metadata: assistant.metadata }
            : {}),
          status,
        };
        const snapshot = JSON.stringify(update);
        if (snapshot !== lastSnapshot) {
          lastSnapshot = snapshot;
          yield update;
        }
        if (!activeStatuses.has(history.status)) return;
        try {
          await waitForHistoryPoll(signal);
        } catch {
          if (signal.aborted) return;
          throw new Error("恢复任务输出时轮询中断");
        }
      }
    },
    async append() {
      // Harness run events are the durable source of truth. The history endpoint
      // reconstructs messages from them, so no second client-side write is needed.
    },
    dispose() {
      disposal.abort(new DOMException("Task view unmounted", "AbortError"));
    },
  };
}

/** How close to the top the reader must scroll before earlier runs load. */
export const LOAD_EARLIER_THRESHOLD_PX = 160;

interface EarlierPagination {
  hasMore: boolean;
  loading: boolean;
  loadEarlier: LoadEarlierFn;
}

/**
 * Pull the previous page in as soon as the reader scrolls to the top, instead
 * of asking for a click. Prepending rows moves the content down, so the scroll
 * offset is restored against the height that was added; a page shorter than the
 * viewport would leave nothing to scroll, so it keeps filling until the list
 * can scroll or the thread runs out of earlier runs.
 */
export function useAutoLoadEarlierMessages(
  frame: RefObject<HTMLElement | null>,
  pagination: EarlierPagination,
  options: { readerDriven?: boolean } = {},
): () => Promise<void> {
  const state = useRef(pagination);
  state.current = pagination;
  const loadPage = useRef<() => Promise<void>>(async () => undefined);

  useEffect(() => {
    const viewport = frame.current?.querySelector<HTMLElement>(".aui-thread-viewport");
    if (!viewport) return;
    let inFlight = false;
    let readerIntentUntil = 0;
    let touchY = 0;

    function keepPlace(previousHeight: number, previousTop: number) {
      const grown = viewport!.scrollHeight - previousHeight;
      if (grown > 0) viewport!.scrollTop = previousTop + grown;
    }

    async function loadEarlierPage() {
      const { hasMore, loading, loadEarlier } = state.current;
      if (inFlight || loading || !hasMore) return;
      inFlight = true;
      const previousHeight = viewport!.scrollHeight;
      const previousTop = viewport!.scrollTop;
      try {
        await loadEarlier();
        // Two frames: assistant-ui re-renders the imported repository first.
        requestAnimationFrame(() => {
          keepPlace(previousHeight, previousTop);
          requestAnimationFrame(() => {
            keepPlace(previousHeight, previousTop);
            fillViewport();
          });
        });
      } finally {
        inFlight = false;
      }
    }
    loadPage.current = loadEarlierPage;

    function fillViewport() {
      if (!options.readerDriven && viewport!.scrollHeight <= viewport!.clientHeight + 1) void loadEarlierPage();
    }

    function onScroll() {
      if (options.readerDriven && performance.now() > readerIntentUntil) return;
      if (viewport!.scrollTop <= LOAD_EARLIER_THRESHOLD_PX) void loadEarlierPage();
    }

    function readEarlier() {
      readerIntentUntil = performance.now() + 800;
      onScroll();
    }
    const onWheel = (event: WheelEvent) => { if (event.deltaY < 0) readEarlier(); };
    const onKey = (event: KeyboardEvent) => { if (["ArrowUp", "PageUp", "Home"].includes(event.key)) readEarlier(); };
    const onTouchStart = (event: TouchEvent) => { touchY = event.touches[0]?.clientY ?? 0; };
    const onTouchMove = (event: TouchEvent) => { if ((event.touches[0]?.clientY ?? 0) > touchY) readEarlier(); };

    viewport.addEventListener("scroll", onScroll, { passive: true });
    viewport.addEventListener("wheel", onWheel, { passive: true });
    viewport.addEventListener("keydown", onKey);
    viewport.addEventListener("touchstart", onTouchStart, { passive: true });
    viewport.addEventListener("touchmove", onTouchMove, { passive: true });
    fillViewport();
    return () => {
      loadPage.current = async () => undefined;
      viewport.removeEventListener("scroll", onScroll);
      viewport.removeEventListener("wheel", onWheel);
      viewport.removeEventListener("keydown", onKey);
      viewport.removeEventListener("touchstart", onTouchStart);
      viewport.removeEventListener("touchmove", onTouchMove);
    };
  }, [frame, pagination.hasMore, pagination.loading, options.readerDriven]);
  return useCallback(() => loadPage.current(), []);
}

/** Create a durable independent snapshot; never re-use the source SDK session. */
export async function forkTask(threadId: string, userId?: string): Promise<TaskSummary> {
  const response = requireAuthenticatedResponse(await fetch(
    `/api/agui/threads/${encodeURIComponent(threadId)}/fork`, { method: "POST" },
  ));
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail ?? body?.error?.message ?? "创建聊天分支失败，请重试。");
  }
  const task = await response.json() as TaskSummary;
  try {
    if (userId) {
      const scoped = createUserScopedStorage(localStorage, userId);
      saveTaskModelOverride(scoped, task.thread_id, loadTaskModelOverride(scoped, threadId));
    }
    const knowledge = localStorage.getItem(`harness:thread-knowledge:${threadId}`);
    if (knowledge) localStorage.setItem(`harness:thread-knowledge:${task.thread_id}`, knowledge);
  } catch { /* Browser preferences cannot invalidate a durable branch. */ }
  notifyTaskListChanged();
  return task;
}
