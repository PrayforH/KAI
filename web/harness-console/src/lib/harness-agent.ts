import {
  type AgentSubscriber,
  HttpAgent,
  type HttpAgentConfig,
  type RunAgentParameters,
  type RunAgentInput,
  type RunAgentResult,
} from "@ag-ui/client";
import { runActivitySchema } from "./activity-schema";
import {
  type ActivityPatchOperation,
  activityStore,
} from "./activity-store";
import { liveResponseStore } from "./live-response-store";
import { runStreamStore } from "./run-stream-store";
import { runReuseStore } from "./run-reuse-store";
import { redirectOnUnauthorized } from "./client-auth";

export interface HarnessHttpAgentConfig extends HttpAgentConfig {
  cancelFetch?: typeof fetch;
  modelRouteOverride?: string | null;
  onRunSucceeded?: () => void;
}

interface AssistantUiRunOptions {
  signal?: AbortSignal;
}

type ActiveRun = Pick<RunAgentInput, "threadId" | "runId"> & {
  idKind: "client" | "server";
};

const terminalAguiEvents = new Set(["RUN_FINISHED", "RUN_ERROR"]);

function replayUrl(requestUrl: string, serverRunId: string) {
  const relative = requestUrl.startsWith("/");
  const base = globalThis.location?.origin ?? "http://localhost";
  const url = new URL(requestUrl, base);
  url.search = "";
  url.pathname = `${url.pathname.replace(/\/$/, "")}/runs/${encodeURIComponent(
    serverRunId,
  )}/events`;
  return relative ? `${url.pathname}${url.search}` : url.toString();
}

function abortError(signal: AbortSignal) {
  if (signal.reason instanceof Error) return signal.reason;
  return new DOMException("The operation was aborted", "AbortError");
}

function waitForReplay(signal: AbortSignal, milliseconds: number) {
  return new Promise<void>((resolve, reject) => {
    if (signal.aborted) {
      reject(abortError(signal));
      return;
    }
    const timer = globalThis.setTimeout(done, milliseconds);
    function done() {
      signal.removeEventListener("abort", cancelled);
      resolve();
    }
    function cancelled() {
      globalThis.clearTimeout(timer);
      reject(abortError(signal));
    }
    signal.addEventListener("abort", cancelled, { once: true });
  });
}

function recoverInterruptedAguiStream(
  response: Response,
  requestUrl: string,
  requestInit: RequestInit,
  fetcher: (url: string, init: RequestInit) => Promise<Response>,
) {
  const serverRunId = response.headers.get("X-Harness-Run-ID");
  if (
    !response.ok ||
    !response.body ||
    !serverRunId ||
    requestInit.method?.toUpperCase() !== "POST" ||
    !response.headers.get("content-type")?.includes("text/event-stream")
  ) {
    return response;
  }

  const recoveryController = new AbortController();
  const signal = requestInit.signal
    ? AbortSignal.any([requestInit.signal, recoveryController.signal])
    : recoveryController.signal;
  const target = replayUrl(requestUrl, serverRunId);
  const encoder = new TextEncoder();
  let currentReader: ReadableStreamDefaultReader<Uint8Array> | undefined;
  let lastEventId = "";
  let terminal = false;

  const body = new ReadableStream<Uint8Array>({
    async start(controller) {
      try {
        let source: Response = response;
        let firstConnection = true;
        while (!terminal) {
          if (!source.body) throw new Error("AG-UI replay response has no body");
          currentReader = source.body.getReader();
          const decoder = new TextDecoder();
          let buffer = "";
          while (true) {
            if (signal.aborted) throw abortError(signal);
            const { done, value } = await currentReader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            while (true) {
              const boundary = buffer.match(/\r?\n\r?\n/);
              if (!boundary || boundary.index === undefined) break;
              const frame = buffer.slice(0, boundary.index);
              buffer = buffer.slice(boundary.index + boundary[0].length);
              const lines = frame.split(/\r?\n/);
              const id = lines.find((line) => line.startsWith("id:"))
                ?.slice(3).trim();
              const data = lines
                .filter((line) => line.startsWith("data:"))
                .map((line) => line.slice(5).trimStart())
                .join("\n");
              if (id) lastEventId = id;
              if (data) {
                try {
                  const event = JSON.parse(data) as { type?: string };
                  if (event.type && terminalAguiEvents.has(event.type)) terminal = true;
                } catch {
                  // The AG-UI parser remains authoritative for schema errors.
                }
              }
              controller.enqueue(encoder.encode(`${frame}\n\n`));
            }
          }
          currentReader = undefined;
          if (terminal) break;
          if (!firstConnection) await waitForReplay(signal, 350);
          firstConnection = false;
          const headers = new Headers(requestInit.headers);
          headers.delete("content-type");
          headers.set("accept", "text/event-stream");
          if (lastEventId) headers.set("last-event-id", lastEventId);
          source = await fetcher(target, {
            method: "GET",
            headers,
            cache: "no-store",
            signal,
          });
          redirectOnUnauthorized(source);
          if (!source.ok) {
            throw new Error(`AG-UI replay failed: ${source.status}`);
          }
        }
        controller.close();
      } catch (error) {
        controller.error(error);
      }
    },
    async cancel(reason) {
      recoveryController.abort(reason);
      await currentReader?.cancel(reason);
    },
  });

  return new Response(body, {
    status: response.status,
    statusText: response.statusText,
    headers: response.headers,
  });
}

export class HarnessHttpAgent extends HttpAgent {
  private activeInput?: ActiveRun;
  private cancelFetch: typeof fetch;
  private modelRouteOverride?: string;
  private onRunSucceeded?: () => void;

  constructor(config: HarnessHttpAgentConfig) {
    const {
      cancelFetch,
      modelRouteOverride,
      onRunSucceeded,
      ...httpConfig
    } = config;
    const transportFetch = httpConfig.fetch ?? globalThis.fetch.bind(globalThis);
    const sessionAwareFetch: typeof transportFetch = async (url, init) => {
      let response = await transportFetch(url, init);
      redirectOnUnauthorized(response);
      if (response.headers.get("X-Harness-Run-Reused") === "true") {
        const runId = response.headers.get("X-Harness-Run-ID");
        const canonicalClientRunId = response.headers.get(
          "X-Harness-Canonical-Client-Run-ID",
        );
        if (runId && canonicalClientRunId) {
          runReuseStore.show({ runId, canonicalClientRunId });
        }
      } else {
        runReuseStore.clear();
      }
      response = recoverInterruptedAguiStream(
        response,
        String(url),
        init ?? {},
        sessionAwareFetch,
      );
      return response;
    };
    super({ ...httpConfig, fetch: sessionAwareFetch });
    this.modelRouteOverride = modelRouteOverride || undefined;
    this.onRunSucceeded = onRunSucceeded;
    const cancelTransport = cancelFetch ?? globalThis.fetch.bind(globalThis);
    this.cancelFetch = async (input, init) => {
      const response = await cancelTransport(input, init);
      redirectOnUnauthorized(response);
      return response;
    };
  }

  override run(input: RunAgentInput) {
    this.activeInput = {
      threadId: input.threadId,
      runId: input.runId,
      idKind: "client",
    };
    return super.run(this.withModelOverride(input));
  }

  adoptActiveRun(threadId: string, serverRunId: string): void {
    this.activeInput = {
      threadId,
      runId: serverRunId,
      idKind: "server",
    };
  }

  private withModelOverride<T extends object>(input: T): T {
    if (!this.modelRouteOverride) return input;
    const current = input as T & { forwardedProps?: Record<string, unknown> };
    return {
      ...input,
      forwardedProps: {
        ...current.forwardedProps,
        modelRoute: this.modelRouteOverride,
      },
    };
  }

  override runAgent(
    parameters?: RunAgentParameters,
    subscriber?: AgentSubscriber,
    options?: AssistantUiRunOptions,
  ): Promise<RunAgentResult> {
    const input = parameters as Partial<RunAgentInput> | undefined;
    let activeInput: ActiveRun | undefined;
    if (input?.runId) {
      activeInput = {
        threadId: input.threadId || this.threadId || "main",
        runId: input.runId,
        idKind: "client",
      };
      this.activeInput = activeInput;
    }
    const runtimeThreadId = activeInput?.threadId ?? this.threadId;
    const wrapped: AgentSubscriber = {
      ...subscriber,
      onRunStartedEvent: async (params) => {
        liveResponseStore.startRun(params.event.runId, runtimeThreadId);
        runStreamStore.startRun(params.event.runId, runtimeThreadId);
        return subscriber?.onRunStartedEvent?.(params);
      },
      onTextMessageStartEvent: async (params) => {
        liveResponseStore.startMessage(params.event.messageId, runtimeThreadId);
        return subscriber?.onTextMessageStartEvent?.(params);
      },
      onTextMessageContentEvent: async (params) => {
        liveResponseStore.append(
          params.event.messageId,
          params.event.delta,
          runtimeThreadId,
        );
        return subscriber?.onTextMessageContentEvent?.(params);
      },
      onTextMessageEndEvent: async (params) => {
        liveResponseStore.completeMessage(params.event.messageId, runtimeThreadId);
        return subscriber?.onTextMessageEndEvent?.(params);
      },
      onToolCallStartEvent: async (params) => {
        // Artifact presentation is a response deliverable, not another model
        // action. Keeping the final response active also lets the UI render
        // that prose immediately before the generated file card.
        if (params.event.toolCallName !== "harness_present_artifact") {
          liveResponseStore.hideForTool(runtimeThreadId);
        }
        return subscriber?.onToolCallStartEvent?.(params);
      },
      onRunFinishedEvent: async (params) => {
        liveResponseStore.completeRun(runtimeThreadId);
        runStreamStore.completeRun(params.event.runId, runtimeThreadId);
        const result = await subscriber?.onRunFinishedEvent?.(params);
        this.onRunSucceeded?.();
        return result;
      },
      onRunErrorEvent: async (params) => {
        liveResponseStore.failRun(runtimeThreadId);
        runStreamStore.failRun(
          typeof params.event.runId === "string"
            ? params.event.runId
            : undefined,
          runtimeThreadId,
        );
        return subscriber?.onRunErrorEvent?.(params);
      },
      onActivitySnapshotEvent: async (params) => {
        if (params.event.activityType === "harness.run.v1") {
          const parsed = runActivitySchema.safeParse(params.event.content);
          if (parsed.success) activityStore.publish(parsed.data, runtimeThreadId);
        }
        return subscriber?.onActivitySnapshotEvent?.(params);
      },
      onActivityDeltaEvent: async (params) => {
        if (params.event.activityType === "harness.run.v1") {
          activityStore.patch(
            params.event.patch as readonly ActivityPatchOperation[],
            runtimeThreadId,
          );
        }
        return subscriber?.onActivityDeltaEvent?.(params);
      },
    };
    const signal = options?.signal;
    const cancelFromSignal = () => this.cancelActiveRun();
    if (signal?.aborted) cancelFromSignal();
    else signal?.addEventListener("abort", cancelFromSignal, { once: true });

    const routedParameters = parameters
      ? this.withModelOverride(parameters)
      : parameters;
    return super.runAgent(routedParameters, wrapped)
      .catch((error: unknown) => {
        liveResponseStore.failRun(runtimeThreadId);
        runStreamStore.failRun(activeInput?.runId, runtimeThreadId);
        throw error;
      })
      .finally(() => {
        signal?.removeEventListener("abort", cancelFromSignal);
        if (
          activeInput &&
          this.activeInput?.threadId === activeInput.threadId &&
          this.activeInput.runId === activeInput.runId
        ) {
          this.activeInput = undefined;
        }
      });
  }

  cancelActiveRun(): void {
    const activeInput = this.activeInput;
    liveResponseStore.completeRun(activeInput?.threadId);
    runStreamStore.completeRun(activeInput?.runId, activeInput?.threadId);
    this.activeInput = undefined;
    if (!activeInput) return;
    const relative = this.url.startsWith("/");
    const base = globalThis.location?.origin ?? "http://localhost";
    const url = new URL(this.url, base);
    url.search = "";
    url.pathname = activeInput.idKind === "server"
      ? `${url.pathname.replace(/\/$/, "")}/runs/${encodeURIComponent(
          activeInput.runId,
        )}/cancel`
      : `${url.pathname.replace(/\/$/, "")}/threads/${encodeURIComponent(
          activeInput.threadId,
        )}/runs/${encodeURIComponent(activeInput.runId)}/cancel`;
    const target = relative ? `${url.pathname}${url.search}` : url.toString();
    void this.cancelFetch(target, {
      method: "POST",
      headers: this.headers,
    }).catch((error: unknown) => {
      console.error("[Harness Console] Failed to cancel Harness run", error);
    });
  }

  override abortRun(): void {
    this.cancelActiveRun();
    super.abortRun();
  }

  override clone(): HarnessHttpAgent {
    const cloned = super.clone() as HarnessHttpAgent;
    cloned.cancelFetch = this.cancelFetch;
    cloned.modelRouteOverride = this.modelRouteOverride;
    cloned.onRunSucceeded = this.onRunSucceeded;
    cloned.activeInput = this.activeInput ? { ...this.activeInput } : undefined;
    return cloned;
  }
}
