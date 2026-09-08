import type { ChatModelRunOptions } from "@assistant-ui/core";
import { afterEach, describe, expect, it, vi } from "vitest";
import { activityStore } from "../src/lib/activity-store";
import {
  createThreadHistoryAdapter,
  invalidateThreadHistory,
  loadTasks,
  prefetchThreadHistory,
  TASK_LIST_REQUEST_TIMEOUT_MS,
} from "../src/lib/task-history";

describe("thread history activity restoration", () => {
  afterEach(() => {
    activityStore.clear();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  function historyResponse(status: string, text: string) {
    return new Response(
      JSON.stringify({
        thread_id: "thread-resume",
        status,
        run_id: "run-resume",
        messages: [
          { id: "user-run-resume", role: "user", content: "继续执行" },
          {
            id: "assistant-run-resume",
            role: "assistant",
            content: text,
          },
        ],
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  }

  it("publishes the durable run activity when a completed task reloads", async () => {
    const activity = {
      run_id: "run-history",
      status: "succeeded",
      started_at: "2026-07-16T00:00:00Z",
      items: [],
      metrics: { turns: 2 },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            thread_id: "thread-history",
            messages: [
              { id: "user-1", role: "user", content: "你好" },
              {
                id: "assistant-1",
                role: "assistant",
                content: "你好！",
                toolCalls: [
                  {
                    id: "harness-activity-run-history",
                    type: "function",
                    function: {
                      name: "harness_run_activity",
                      arguments: JSON.stringify({ activity }),
                    },
                  },
                ],
              },
              {
                id: "tool-activity-run-history",
                role: "tool",
                content: '{"status":"ready"}',
                toolCallId: "harness-activity-run-history",
              },
            ],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      ),
    );

    await createThreadHistoryAdapter("thread-history").load();

    expect(activityStore.getSnapshot()).toMatchObject({
      run_id: "run-history",
      status: "succeeded",
      metrics: { turns: 2 },
    });
  });

  it("coalesces simultaneous recent-task requests during workspace restore", async () => {
    let resolveFetch: ((response: Response) => void) | undefined;
    const fetchMock = vi.fn(
      () => new Promise<Response>((resolve) => { resolveFetch = resolve; }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const fromBindingRestore = loadTasks(false);
    const fromSidebar = loadTasks(false);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fromSidebar).toBe(fromBindingRestore);
    resolveFetch?.(Response.json([]));
    await expect(fromBindingRestore).resolves.toEqual([]);
    await expect(loadTasks(false)).resolves.toEqual([]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("prefetches a completed conversation and reuses it on selection", async () => {
    const fetchMock = vi.fn().mockResolvedValue(historyResponse("succeeded", "预取完成"));
    vi.stubGlobal("fetch", fetchMock);

    await prefetchThreadHistory("thread-prefetched");
    const repository = await createThreadHistoryAdapter("thread-prefetched").load();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(repository.messages.at(-1)?.message.content).toEqual([
      { type: "text", text: "预取完成" },
    ]);
  });

  it("invalidates a prefetched conversation after a new run finishes", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(historyResponse("succeeded", "旧回答"))
      .mockResolvedValueOnce(historyResponse("succeeded", "新回答"));
    vi.stubGlobal("fetch", fetchMock);

    await prefetchThreadHistory("thread-invalidated");
    invalidateThreadHistory("thread-invalidated");
    await createThreadHistoryAdapter("thread-invalidated").load();

    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("times out a stalled task-list request and allows the next refresh to recover", async () => {
    vi.useFakeTimers();
    const fetchMock = vi.fn((_input: RequestInfo | URL, init?: RequestInit) =>
      new Promise<Response>((_resolve, reject) => {
        init?.signal?.addEventListener("abort", () => {
          reject(new DOMException("aborted", "AbortError"));
        }, { once: true });
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const stalled = loadTasks(true);
    const timedOut = expect(stalled).rejects.toThrow("任务列表读取超时");
    await vi.advanceTimersByTimeAsync(TASK_LIST_REQUEST_TIMEOUT_MS);
    await timedOut;

    fetchMock.mockResolvedValueOnce(Response.json([]));
    await expect(loadTasks(true)).resolves.toEqual([]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("loads an active turn directly into one resumed response slot", async () => {
    const onActiveRun = vi.fn();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(historyResponse("running", "已完成一半")));

    const repository = await createThreadHistoryAdapter("thread-resume", {
      onActiveRun,
    }).load();

    expect(repository.unstable_resume).toBe(true);
    expect(repository.headId).toBe("user-run-resume");
    expect(repository.messages.map((item) => item.message.id)).toEqual([
      "user-run-resume",
    ]);
    expect(onActiveRun).toHaveBeenCalledWith("run-resume");
  });

  it("recovers full snapshots when the runtime does not resume the history adapter", async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(historyResponse("running", "第一段"))
      .mockResolvedValueOnce(historyResponse("succeeded", "第一段第二段"));
    vi.stubGlobal("fetch", fetchMock);
    const adapter = createThreadHistoryAdapter("thread-snapshot-recovery");
    const active = await adapter.loadSnapshot();
    expect(active.messages.map((item) => item.message.id)).toEqual(["user-run-resume", "assistant-run-resume"]);
    const final = await adapter.loadSnapshot();
    expect(final.messages.at(-1)?.message.content).toEqual([{ type: "text", text: "第一段第二段" }]);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    adapter.dispose();
  });

  it("keeps refreshing the resumed response until the run is terminal", async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(historyResponse("running", "第一段"))
      .mockResolvedValueOnce(historyResponse("succeeded", "第一段第二段"));
    vi.stubGlobal("fetch", fetchMock);
    const adapter = createThreadHistoryAdapter("thread-resume");
    const abortController = new AbortController();
    const iterator = adapter.resume!({
      abortSignal: abortController.signal,
    } as ChatModelRunOptions);

    const first = await iterator.next();
    expect(first.value).toMatchObject({
      content: [{ type: "text", text: "第一段" }],
      status: { type: "running" },
    });

    const terminalPromise = iterator.next();
    await vi.advanceTimersByTimeAsync(500);
    const terminal = await terminalPromise;
    expect(terminal.value).toMatchObject({
      content: [{ type: "text", text: "第一段第二段" }],
      status: { type: "complete" },
    });
    expect((await iterator.next()).done).toBe(true);
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
