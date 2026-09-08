import {
  ExportedMessageRepository,
  type ChatModelRunOptions,
  type ChatModelRunResult,
  type ThreadHistoryAdapter,
} from "@assistant-ui/core";
import { fromAgUiMessages } from "@assistant-ui/react-ag-ui";
import type { ApprovalDetails } from "../components/approval-card";
import { latestHistoryRunActivity } from "./activity-schema";
import { activityStore } from "./activity-store";
import { requireAuthenticatedResponse } from "./client-auth";

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
  last_read_at?: string | null;
  pending_approval?: (ApprovalDetails & { status: string }) | null;
}

const taskListRequests = new Map<boolean, Promise<TaskSummary[]>>();
const taskListSnapshots = new Map<boolean, { receivedAt: number; tasks: TaskSummary[] }>();
const taskListCoalesceMs = 250;
export const TASK_LIST_REQUEST_TIMEOUT_MS = 8_000;
export const THREAD_HISTORY_PREFETCH_TTL_MS = 30_000;
const threadHistoryCacheMax = 8;

interface ThreadHistoryResponse {
  thread_id: string;
  status: string;
  run_id?: string | null;
  messages: Array<{
    id: string;
    role: string;
    content: string;
    toolCalls?: unknown[];
    tool_calls?: unknown[];
    toolCallId?: string;
    tool_call_id?: string;
  }>;
}

const activeStatuses = new Set([
  "queued",
  "provisioning",
  "running",
  "waiting_approval",
  "cancelling",
]);

const threadHistoryRequests = new Map<string, Promise<ThreadHistoryResponse | null>>();
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

function historyUrl(threadId: string) {
  return `/api/agui/threads/${encodeURIComponent(threadId)}/history`;
}

async function loadThreadHistory(
  threadId: string,
  signal?: AbortSignal,
): Promise<ThreadHistoryResponse | null> {
  const snapshot = threadHistorySnapshots.get(threadId);
  if (snapshot && Date.now() - snapshot.receivedAt < THREAD_HISTORY_PREFETCH_TTL_MS) {
    threadHistorySnapshots.delete(threadId);
    threadHistorySnapshots.set(threadId, snapshot);
    return snapshot.history;
  }
  if (snapshot) threadHistorySnapshots.delete(threadId);

  let request = threadHistoryRequests.get(threadId);
  if (!request) {
    request = fetch(historyUrl(threadId), { cache: "no-store" })
      .then(requireAuthenticatedResponse)
      .then(async (response) => {
        if (response.status === 404) return null;
        if (!response.ok) {
          throw new Error((await response.text()) || `HTTP ${response.status}`);
        }
        return response.json() as Promise<ThreadHistoryResponse>;
      })
      .then((history) => {
        if (!history || !activeStatuses.has(history.status)) {
          cacheThreadHistory(threadId, history);
        }
        return history;
      })
      .finally(() => {
        if (threadHistoryRequests.get(threadId) === request) {
          threadHistoryRequests.delete(threadId);
        }
      });
    threadHistoryRequests.set(threadId, request);
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

export function invalidateThreadHistory(threadId: string): void {
  threadHistorySnapshots.delete(threadId);
}

function publishHistoryActivity(history: ThreadHistoryResponse, threadId: string) {
  const restoredActivity = latestHistoryRunActivity(history.messages);
  if (restoredActivity) activityStore.publish(restoredActivity, threadId);
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
      taskListSnapshots.set(archived, { receivedAt: Date.now(), tasks });
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
}

export function createThreadHistoryAdapter(
  threadId: string,
  options: { onActiveRun?: (serverRunId: string) => void } = {},
): ThreadHistoryAdapter & { dispose(): void; loadSnapshot(onLoaded?: (repository: ReturnType<typeof ExportedMessageRepository.fromArray>) => void): Promise<ReturnType<typeof ExportedMessageRepository.fromArray>> } {
  const disposal = new AbortController();
  return {
    async loadSnapshot(onLoaded) {
      invalidateThreadHistory(threadId);
      const history = await loadThreadHistory(threadId, disposal.signal);
      const repository = ExportedMessageRepository.fromArray(history ? fromAgUiMessages(history.messages, { showThinking: true }) : []);
      // Import terminal text before publishing the phase that stops recovery polling.
      onLoaded?.(repository);
      if (history) publishHistoryActivity(history, threadId);
      return repository;
    },
    async load() {
      const history = await loadThreadHistory(threadId, disposal.signal);
      if (!history) {
        return ExportedMessageRepository.fromArray([]);
      }
      publishHistoryActivity(history, threadId);
      const repository = ExportedMessageRepository.fromArray(
        fromAgUiMessages(history.messages, { showThinking: true }),
      );
      if (!history.run_id || !activeStatuses.has(history.status)) {
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
        publishHistoryActivity(history, threadId);
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
