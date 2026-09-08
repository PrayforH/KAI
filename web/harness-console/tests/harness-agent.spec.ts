import { describe, expect, it, vi } from "vitest";
import type { RunAgentInput } from "@ag-ui/client";
import { HarnessHttpAgent } from "../src/lib/harness-agent";
import { liveResponseStore } from "../src/lib/live-response-store";
import { runStreamStore } from "../src/lib/run-stream-store";

describe("HarnessHttpAgent", () => {
  it("adds the task model override to AG-UI forwarded props", async () => {
    let requestBody: Record<string, unknown> | undefined;
    const streamFetch: typeof fetch = async (_input, init) => {
      requestBody = JSON.parse(String(init?.body)) as Record<string, unknown>;
      return new Response(
        [
          'data: {"type":"RUN_STARTED","threadId":"thread-model","runId":"run-model"}',
          "",
          'data: {"type":"RUN_FINISHED","threadId":"thread-model","runId":"run-model"}',
          "",
          "",
        ].join("\n"),
        { headers: { "Content-Type": "text/event-stream" } },
      );
    };
    const agent = new HarnessHttpAgent({
      url: "http://harness/v1/agui",
      fetch: streamFetch,
      modelRouteOverride: "minimax-m3",
      threadId: "thread-model",
    });

    await agent.runAgent({
      runId: "run-model",
      forwardedProps: { existing: true },
    });

    expect(requestBody?.forwardedProps).toEqual({
      existing: true,
      modelRoute: "minimax-m3",
    });
  });

  it("requests a durable-history refresh after a run succeeds", async () => {
    const onRunSucceeded = vi.fn();
    const agent = new HarnessHttpAgent({
      url: "http://harness/v1/agui",
      onRunSucceeded,
      fetch: async () => new Response(
        [
          'data: {"type":"RUN_STARTED","threadId":"thread-history","runId":"run-history"}',
          "",
          'data: {"type":"RUN_FINISHED","threadId":"thread-history","runId":"run-history"}',
          "",
          "",
        ].join("\n"),
        { headers: { "Content-Type": "text/event-stream" } },
      ),
    });

    await agent.runAgent({ runId: "run-history" });

    expect(onRunSucceeded).toHaveBeenCalledTimes(1);
  });

  it("notifies Harness when CopilotRuntime stops an active thread", () => {
    let cancelUrl = "";
    let cancelInit: RequestInit | undefined;
    const cancelFetch: typeof fetch = async (input, init) => {
      cancelUrl = String(input);
      cancelInit = init;
      return new Response(null, { status: 202 });
    };
    const agent = new HarnessHttpAgent({
      url: "http://harness/v1/agui?agent_name=echo-agent&agent_version=0.1.0",
      headers: { "X-Tenant-ID": "local", "X-User-ID": "developer" },
      cancelFetch,
    });
    const input: RunAgentInput = {
      threadId: "thread/1",
      runId: "run/1",
      state: {},
      messages: [],
      tools: [],
      context: [],
      forwardedProps: {},
    };

    agent.run(input);
    liveResponseStore.startRun("run/1");
    runStreamStore.startRun("run/1");
    agent.abortRun();

    expect(cancelUrl).toBe(
      "http://harness/v1/agui/threads/thread%2F1/runs/run%2F1/cancel",
    );
    expect(cancelInit).toEqual({
      method: "POST",
      headers: { "X-Tenant-ID": "local", "X-User-ID": "developer" },
    });
    expect(liveResponseStore.getSnapshot().status).toBe("complete");
    expect(runStreamStore.getSnapshot()).toMatchObject({
      runId: "run/1",
      status: "complete",
    });
  });

  it("cancels a resumed run by its durable server ID", () => {
    let cancelUrl = "";
    const agent = new HarnessHttpAgent({
      url: "http://harness/v1/agui?agent_name=echo-agent&agent_version=0.1.0",
      cancelFetch: async (input) => {
        cancelUrl = String(input);
        return new Response(null, { status: 200 });
      },
    });

    agent.adoptActiveRun("thread-resumed", "server/run-resumed");
    agent.cancelActiveRun();

    expect(cancelUrl).toBe(
      "http://harness/v1/agui/runs/server%2Frun-resumed/cancel",
    );
  });

  it("notifies Harness when assistant-ui aborts its run signal", async () => {
    let cancelUrl = "";
    const cancelFetch: typeof fetch = async (input) => {
      cancelUrl = String(input);
      return new Response(null, { status: 202 });
    };
    const streamFetch: typeof fetch = async () =>
      new Response(
        [
          'data: {"type":"RUN_STARTED","threadId":"thread-1","runId":"run-1"}',
          "",
          'data: {"type":"RUN_FINISHED","threadId":"thread-1","runId":"run-1"}',
          "",
          "",
        ].join("\n"),
        { headers: { "Content-Type": "text/event-stream" } },
      );
    const agent = new HarnessHttpAgent({
      url: "http://harness/v1/agui?agent_name=echo-agent&agent_version=0.1.0",
      cancelFetch,
      fetch: streamFetch,
    });
    const input: RunAgentInput = {
      threadId: "thread-1",
      runId: "run-1",
      state: {},
      messages: [],
      tools: [],
      context: [],
      forwardedProps: {},
    };
    const abortController = new AbortController();

    const run = (
      agent.runAgent as unknown as (
        parameters: RunAgentInput,
        subscriber: undefined,
        options: { signal: AbortSignal },
      ) => Promise<unknown>
    )(input, undefined, { signal: abortController.signal });
    abortController.abort();
    await run;

    expect(cancelUrl).toBe(
      "http://harness/v1/agui/threads/thread-1/runs/run-1/cancel",
    );
  });

  it("binds the browser fetch receiver for cancellation", () => {
    const originalFetch = globalThis.fetch;
    let receiver: unknown;
    globalThis.fetch = function (this: unknown) {
      receiver = this;
      return Promise.resolve(new Response(null, { status: 202 }));
    } as typeof fetch;
    try {
      const agent = new HarnessHttpAgent({ url: "http://harness/v1/agui" });
      const input: RunAgentInput = {
        threadId: "thread-1",
        runId: "run-1",
        state: {},
        messages: [],
        tools: [],
        context: [],
        forwardedProps: {},
      };

      agent.run(input);
      agent.abortRun();

      expect(receiver).toBe(globalThis);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("settles local stream state when transport fails before a terminal event", async () => {
    liveResponseStore.clear();
    runStreamStore.clear();
    const agent = new HarnessHttpAgent({
      url: "http://harness/v1/agui",
      fetch: async () => {
        throw new Error("connection closed");
      },
    });

    await expect(agent.runAgent({ runId: "run-disconnected" })).rejects.toThrow(
      "connection closed",
    );

    expect(liveResponseStore.getSnapshot().status).toBe("error");
    expect(runStreamStore.getSnapshot()).toMatchObject({
      runId: "run-disconnected",
      status: "error",
    });

    agent.cancelActiveRun();
    expect(runStreamStore.getSnapshot().status).toBe("complete");
  });

  it("forwards native text deltas while tracking run lifecycle without duplicating text", async () => {
    const lifecycle: string[] = [];
    const deltas: string[] = [];
    liveResponseStore.clear();
    runStreamStore.clear();
    const unsubscribe = runStreamStore.subscribe(() => {
      lifecycle.push(runStreamStore.getSnapshot().status);
    });
    const streamFetch: typeof fetch = async () =>
      new Response(
        [
          'data: {"type":"RUN_STARTED","threadId":"thread-stream","runId":"run-stream"}',
          "",
          'data: {"type":"TEXT_MESSAGE_START","messageId":"message-stream","role":"assistant"}',
          "",
          'data: {"type":"TEXT_MESSAGE_CONTENT","messageId":"message-stream","delta":"第一段"}',
          "",
          'data: {"type":"TEXT_MESSAGE_CONTENT","messageId":"message-stream","delta":"第二段"}',
          "",
          'data: {"type":"TEXT_MESSAGE_END","messageId":"message-stream"}',
          "",
          'data: {"type":"RUN_FINISHED","threadId":"thread-stream","runId":"run-stream"}',
          "",
          "",
        ].join("\n"),
        { headers: { "Content-Type": "text/event-stream" } },
      );
    const agent = new HarnessHttpAgent({
      url: "http://harness/v1/agui",
      fetch: streamFetch,
    });

    await agent.runAgent(
      { runId: "run-stream" },
      {
        onTextMessageContentEvent: ({ event }) => {
          deltas.push(event.delta);
        },
      },
    );

    expect(deltas).toEqual(["第一段", "第二段"]);
    expect(liveResponseStore.getSnapshot()).toMatchObject({
      runId: "run-stream",
      messageId: "message-stream",
      text: "第一段第二段",
      status: "complete",
      visible: true,
    });
    expect(lifecycle).toEqual(["running", "complete"]);
    expect(runStreamStore.getSnapshot()).toMatchObject({
      runId: "run-stream",
      status: "complete",
    });
    unsubscribe();
  });

  it("keeps final text visible when a generated artifact follows it", async () => {
    liveResponseStore.clear();
    const streamFetch: typeof fetch = async () =>
      new Response(
        [
          'data: {"type":"RUN_STARTED","threadId":"thread-artifact","runId":"run-artifact"}',
          "",
          'data: {"type":"TEXT_MESSAGE_START","messageId":"message-final","role":"assistant"}',
          "",
          'data: {"type":"TEXT_MESSAGE_CONTENT","messageId":"message-final","delta":"文件已经生成。"}',
          "",
          'data: {"type":"TEXT_MESSAGE_END","messageId":"message-final"}',
          "",
          'data: {"type":"TOOL_CALL_START","toolCallId":"artifact-1","toolCallName":"harness_present_artifact","parentMessageId":"message-final"}',
          "",
          'data: {"type":"TOOL_CALL_ARGS","toolCallId":"artifact-1","delta":"{\\"artifact_id\\":\\"artifact-1\\"}"}',
          "",
          'data: {"type":"TOOL_CALL_END","toolCallId":"artifact-1"}',
          "",
          'data: {"type":"TOOL_CALL_RESULT","messageId":"artifact-result-1","toolCallId":"artifact-1","content":"{\\"status\\":\\"ready\\"}","role":"tool"}',
          "",
          'data: {"type":"RUN_FINISHED","threadId":"thread-artifact","runId":"run-artifact"}',
          "",
          "",
        ].join("\n"),
        { headers: { "Content-Type": "text/event-stream" } },
      );
    const agent = new HarnessHttpAgent({
      url: "http://harness/v1/agui",
      fetch: streamFetch,
    });

    await agent.runAgent({ runId: "run-artifact" });

    expect(liveResponseStore.getSnapshot()).toMatchObject({
      runId: "run-artifact",
      messageId: "message-final",
      text: "文件已经生成。",
      status: "complete",
      visible: true,
    });
  });

  it("shows a terminal-only response after tools reused the stable message", async () => {
    liveResponseStore.clear();
    const streamFetch: typeof fetch = async () =>
      new Response(
        [
          'data: {"type":"RUN_STARTED","threadId":"thread-terminal","runId":"run-terminal"}',
          "",
          'data: {"type":"TEXT_MESSAGE_START","messageId":"assistant-server-run","role":"assistant"}',
          "",
          'data: {"type":"TOOL_CALL_START","toolCallId":"tool-1","toolCallName":"search","parentMessageId":"assistant-server-run"}',
          "",
          'data: {"type":"TOOL_CALL_ARGS","toolCallId":"tool-1","delta":"{}"}',
          "",
          'data: {"type":"TOOL_CALL_END","toolCallId":"tool-1"}',
          "",
          'data: {"type":"TOOL_CALL_RESULT","messageId":"tool-result-1","toolCallId":"tool-1","content":"ok","role":"tool"}',
          "",
          'data: {"type":"TEXT_MESSAGE_CONTENT","messageId":"assistant-server-run","delta":"## 查询结论\\n\\n最终回答。"}',
          "",
          'data: {"type":"TEXT_MESSAGE_END","messageId":"assistant-server-run"}',
          "",
          'data: {"type":"RUN_FINISHED","threadId":"thread-terminal","runId":"run-terminal"}',
          "",
          "",
        ].join("\n"),
        { headers: { "Content-Type": "text/event-stream" } },
      );
    const agent = new HarnessHttpAgent({
      url: "http://harness/v1/agui",
      fetch: streamFetch,
    });

    await agent.runAgent({ runId: "run-terminal" });

    expect(liveResponseStore.getSnapshot()).toMatchObject({
      messageId: "assistant-server-run",
      text: "## 查询结论\n\n最终回答。",
      status: "complete",
      visible: true,
    });
  });

  it("shows a completed response before a post-processing run error", async () => {
    liveResponseStore.clear();
    const onRunSucceeded = vi.fn();
    const streamFetch: typeof fetch = async () =>
      new Response(
        [
          'data: {"type":"RUN_STARTED","threadId":"thread-error","runId":"run-error"}',
          "",
          'data: {"type":"TEXT_MESSAGE_START","messageId":"assistant-run-error","role":"assistant"}',
          "",
          'data: {"type":"TEXT_MESSAGE_CONTENT","messageId":"assistant-run-error","delta":"图谱已经生成。可下载查看。"}',
          "",
          'data: {"type":"TEXT_MESSAGE_END","messageId":"assistant-run-error"}',
          "",
          'data: {"type":"RUN_ERROR","threadId":"thread-error","runId":"run-error","message":"工作区保存失败"}',
          "",
          "",
        ].join("\n"),
        { headers: { "Content-Type": "text/event-stream" } },
      );
    const agent = new HarnessHttpAgent({
      url: "http://harness/v1/agui",
      fetch: streamFetch,
      onRunSucceeded,
    });

    await agent.runAgent({ runId: "run-error" });

    expect(liveResponseStore.getSnapshot()).toMatchObject({
      messageId: "assistant-run-error",
      text: "图谱已经生成。可下载查看。",
      status: "error",
      visible: true,
    });
    expect(onRunSucceeded).not.toHaveBeenCalled();
  });

  it("publishes the first text chunk before the response stream finishes", async () => {
    vi.useFakeTimers();
    const encoder = new TextEncoder();
    let controller: ReadableStreamDefaultController<Uint8Array> | undefined;
    let resolveFirstChunk: (() => void) | undefined;
    const firstChunk = new Promise<void>((resolve) => {
      resolveFirstChunk = resolve;
    });
    const substantialFirstChunk = "第一段".repeat(60);
    liveResponseStore.clear();
    const unsubscribe = liveResponseStore.subscribe(() => {
      if (liveResponseStore.getSnapshot().text === substantialFirstChunk) {
        resolveFirstChunk?.();
      }
    });
    const streamFetch: typeof fetch = async () =>
      new Response(
        new ReadableStream<Uint8Array>({
          start(nextController) {
            controller = nextController;
            nextController.enqueue(
              encoder.encode(
                [
                  'data: {"type":"RUN_STARTED","threadId":"thread-live","runId":"run-live"}',
                  "",
                  'data: {"type":"TEXT_MESSAGE_START","messageId":"message-live","role":"assistant"}',
                  "",
                  `data: ${JSON.stringify({ type: "TEXT_MESSAGE_CONTENT", messageId: "message-live", delta: substantialFirstChunk })}`,
                  "",
                  "",
                ].join("\n"),
              ),
            );
          },
        }),
        { headers: { "Content-Type": "text/event-stream" } },
      );
    const agent = new HarnessHttpAgent({
      url: "http://harness/v1/agui",
      fetch: streamFetch,
    });

    const run = agent.runAgent({ runId: "run-live" });
    await firstChunk;

    expect(liveResponseStore.getSnapshot()).toMatchObject({
      text: substantialFirstChunk,
      status: "streaming",
      visible: true,
    });

    expect(liveResponseStore.getSnapshot()).toMatchObject({
      text: substantialFirstChunk,
      status: "streaming",
      visible: true,
    });

    controller?.enqueue(
      encoder.encode(
        [
          'data: {"type":"TEXT_MESSAGE_CONTENT","messageId":"message-live","delta":"第二段"}',
          "",
          'data: {"type":"TEXT_MESSAGE_END","messageId":"message-live"}',
          "",
          'data: {"type":"RUN_FINISHED","threadId":"thread-live","runId":"run-live"}',
          "",
          "",
        ].join("\n"),
      ),
    );
    controller?.close();
    await run;

    expect(liveResponseStore.getSnapshot()).toMatchObject({
      text: `${substantialFirstChunk}第二段`,
      status: "complete",
      visible: true,
    });
    unsubscribe();
    vi.useRealTimers();
  });

  it("recovers a prematurely closed live stream without refreshing", async () => {
    liveResponseStore.clear();
    runStreamStore.clear();
    const calls: Array<{ url: string; lastEventId: string | null }> = [];
    const streamFetch: typeof fetch = async (input, init) => {
      const url = String(input);
      calls.push({
        url,
        lastEventId: new Headers(init?.headers).get("last-event-id"),
      });
      if (calls.length === 1) {
        return new Response(
          [
            'id: 1\ndata: {"type":"RUN_STARTED","threadId":"thread-recover","runId":"run-recover"}',
            'id: 2\ndata: {"type":"TEXT_MESSAGE_START","messageId":"message-recover","role":"assistant"}',
            'id: 3\ndata: {"type":"TEXT_MESSAGE_CONTENT","messageId":"message-recover","delta":"第一段"}',
            "",
            "",
          ].join("\n\n"),
          {
            headers: {
              "Content-Type": "text/event-stream",
              "X-Harness-Run-ID": "server-run-recover",
            },
          },
        );
      }
      return new Response(
        [
          'id: 4\ndata: {"type":"TEXT_MESSAGE_CONTENT","messageId":"message-recover","delta":"第二段"}',
          'id: 5\ndata: {"type":"TEXT_MESSAGE_END","messageId":"message-recover"}',
          'id: 6:2\ndata: {"type":"RUN_FINISHED","threadId":"thread-recover","runId":"run-recover"}',
          "",
          "",
        ].join("\n\n"),
        { headers: { "Content-Type": "text/event-stream" } },
      );
    };
    const agent = new HarnessHttpAgent({
      url: "http://harness/v1/agui?agent_name=echo-agent&agent_version=0.1.0",
      fetch: streamFetch,
      threadId: "thread-recover",
    });

    await agent.runAgent({ runId: "run-recover" });

    expect(calls).toEqual([
      {
        url: "http://harness/v1/agui?agent_name=echo-agent&agent_version=0.1.0",
        lastEventId: null,
      },
      {
        url: "http://harness/v1/agui/runs/server-run-recover/events",
        lastEventId: "3",
      },
    ]);
    expect(liveResponseStore.getSnapshot()).toMatchObject({
      text: "第一段第二段",
      status: "complete",
      visible: true,
    });
    expect(runStreamStore.getSnapshot()).toMatchObject({
      runId: "run-recover",
      status: "complete",
    });
  });
});
