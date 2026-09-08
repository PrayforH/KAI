import {
  context,
  propagation,
  SpanKind,
  SpanStatusCode,
  trace,
  type Span,
} from "@opentelemetry/api";
import type { HarnessServerConfig } from "./server-config";
import {
  ACCESS_COOKIE,
  appendClearedSessionCookies,
  appendSessionCookies,
  readCookie,
  refreshSession,
} from "./auth-session";

const RESPONSE_HEADERS = [
  "cache-control",
  "content-disposition",
  "content-type",
  "etag",
  "vary",
  "www-authenticate",
  "x-accel-buffering",
  "x-agent-content-sha256",
  "x-agent-package-sha256",
  "x-harness-canonical-client-run-id",
  "x-harness-run-deduplicated",
  "x-harness-run-id",
  "x-harness-run-reused",
  "x-harness-auth-error",
  "x-inference-time-s",
  "x-request-id",
];

const TRACE_HEADERS = ["traceparent", "tracestate", "baggage"] as const;
const tracer = trace.getTracer("claude-agent-harness-web");
const headerSetter = {
  set(carrier: Headers, key: string, value: string) {
    carrier.set(key, value);
  },
};
const headerGetter = {
  get(carrier: Headers, key: string) {
    return carrier.get(key) ?? undefined;
  },
  keys(carrier: Headers) {
    return [...carrier.keys()];
  },
};

function upstreamHeaders(
  request: Request,
  config: HarnessServerConfig,
  accessToken = readCookie(request, ACCESS_COOKIE),
) {
  const headers = new Headers({
    ...config.serviceHeaders,
  });
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  for (const name of ["accept", "content-type", "last-event-id"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  for (const name of TRACE_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  propagation.inject(context.active(), headers, headerSetter);
  return headers;
}

function responseHeaders(upstream: Response) {
  const headers = new Headers();
  for (const name of RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) headers.set(name, value);
  }
  return headers;
}

async function requestBody(request: Request): Promise<ArrayBuffer | undefined> {
  if (request.method === "GET" || request.method === "HEAD") return undefined;
  const body = await request.arrayBuffer();
  return body.byteLength ? body : undefined;
}

function unavailableResponse() {
  return Response.json(
    {
      error: {
        code: "harness_unavailable",
        message: "Harness API 当前不可用，请确认本地服务已经启动。",
      },
    },
    { status: 502 },
  );
}

function tracedResponse(response: Response, span: Span) {
  span.setAttribute("http.response.status_code", response.status);
  if (response.status >= 500) {
    span.setStatus({ code: SpanStatusCode.ERROR });
  }
  if (!response.body) {
    span.end();
    return response;
  }

  const reader = response.body.getReader();
  let ended = false;
  const finish = () => {
    if (ended) return;
    ended = true;
    span.end();
  };
  const body = new ReadableStream<Uint8Array>({
    async pull(controller) {
      try {
        const item = await reader.read();
        if (item.done) {
          finish();
          controller.close();
          return;
        }
        controller.enqueue(item.value);
      } catch (error) {
        span.addEvent("stream.error", {
          "error.type": error instanceof Error ? error.name : "StreamError",
        });
        span.setStatus({ code: SpanStatusCode.ERROR });
        finish();
        controller.error(error);
      }
    },
    async cancel(reason) {
      span.addEvent("stream.cancelled");
      try {
        await reader.cancel(reason);
      } finally {
        finish();
      }
    },
  });
  return new Response(body, {
    status: response.status,
    statusText: response.statusText,
    headers: response.headers,
  });
}

async function forward(
  request: Request,
  url: string,
  config: HarnessServerConfig,
  fetcher: typeof fetch,
  spanName: string,
) {
  const parent = propagation.extract(
    context.active(),
    request.headers,
    headerGetter,
  );
  return context.with(parent, () =>
    tracer.startActiveSpan(
      spanName,
      {
        kind: SpanKind.SERVER,
        attributes: {
          "http.request.method": request.method,
          "url.path": new URL(request.url).pathname,
          "harness.proxy.target": "api",
        },
      },
      async (span) => {
        try {
          const body = await requestBody(request);
          let upstream = await fetcher(url, {
            method: request.method,
            headers: upstreamHeaders(request, config),
            body,
            cache: "no-store",
            signal: request.signal,
          });
          let refreshed;
          if (upstream.status === 401) {
            refreshed = await refreshSession(request, config, fetcher);
            if (refreshed) {
              upstream = await fetcher(url, {
                method: request.method,
                headers: upstreamHeaders(request, config, refreshed.access_token),
                body,
                cache: "no-store",
                signal: request.signal,
              });
            }
          }
          const headers = responseHeaders(upstream);
          if (refreshed) appendSessionCookies(headers, refreshed, config);
          else if (upstream.status === 401) appendClearedSessionCookies(headers, config);
          return tracedResponse(
            new Response(upstream.body, {
              status: upstream.status,
              statusText: upstream.statusText,
              headers,
            }),
            span,
          );
        } catch (error) {
          span.addEvent("proxy.error", {
            "error.type": error instanceof Error ? error.name : "ProxyError",
          });
          span.setStatus({ code: SpanStatusCode.ERROR });
          return tracedResponse(unavailableResponse(), span);
        }
      }
    ),
  );
}

export async function proxyAguiRequest(
  request: Request,
  config: HarnessServerConfig,
  fetcher: typeof fetch = fetch,
  path = "",
) {
  const url = new URL(config.aguiUrl);
  if (path) {
    url.pathname = `${url.pathname.replace(/\/$/, "")}/${path.replace(/^\//, "")}`;
    url.search = new URL(request.url).search;
  } else {
    const requested = new URL(request.url).searchParams;
    const agentName = requested.get("agent_name");
    const agentVersion = requested.get("agent_version");
    const agentOwnerUserId = requested.get("agent_owner_user_id");
    const spaceId = requested.get("space_id");
    if (
      agentName &&
      agentVersion &&
      /^[a-z][a-z0-9-]*$/.test(agentName) &&
      agentVersion.length <= 64
    ) {
      url.searchParams.set("agent_name", agentName);
      url.searchParams.set("agent_version", agentVersion);
      if (agentOwnerUserId && agentOwnerUserId.length <= 128) {
        url.searchParams.set("agent_owner_user_id", agentOwnerUserId);
      }
      if (spaceId && spaceId.length <= 128) {
        url.searchParams.set("space_id", spaceId);
      }
    }
  }
  const spanName =
    request.method === "POST" && !path
      ? "harness.web.question"
      : "harness.web.agui";
  return forward(request, url.toString(), config, fetcher, spanName);
}

export async function proxyInputArtifactRequest(
  request: Request,
  config: HarnessServerConfig,
  fetcher: typeof fetch = fetch,
) {
  return forward(
    request,
    `${config.apiUrl}/v1/input-artifacts`,
    config,
    fetcher,
    "harness.web.input_artifact",
  );
}

export async function proxyAgentCatalogRequest(
  request: Request,
  config: HarnessServerConfig,
  fetcher: typeof fetch = fetch,
  path = "",
) {
  const url = new URL(
    `${config.apiUrl}/v1/agents${path ? `/${path.replace(/^\//, "")}` : ""}`,
  );
  url.search = new URL(request.url).search;
  return forward(
    request,
    url.toString(),
    config,
    fetcher,
    "harness.web.agent_catalog",
  );
}

export async function proxyStudioRequest(
  request: Request,
  config: HarnessServerConfig,
  fetcher: typeof fetch = fetch,
  path = "",
) {
  const url = new URL(
    `${config.apiUrl}/v1/studio${path ? `/${path.replace(/^\//, "")}` : ""}`,
  );
  url.search = new URL(request.url).search;
  return forward(request, url.toString(), config, fetcher, "harness.web.studio");
}

export async function proxyTeamSpaceRequest(
  request: Request,
  config: HarnessServerConfig,
  fetcher: typeof fetch = fetch,
  path = "",
) {
  const url = new URL(
    `${config.apiUrl}/v1/spaces${path ? `/${path.replace(/^\//, "")}` : ""}`,
  );
  url.search = new URL(request.url).search;
  return forward(request, url.toString(), config, fetcher, "harness.web.team_space");
}

export async function proxyAgentTriggerRequest(
  request: Request,
  config: HarnessServerConfig,
  fetcher: typeof fetch = fetch,
  path = "",
) {
  const url = new URL(
    `${config.apiUrl}/webhooks/agent-triggers${path ? `/${path.replace(/^\//, "")}` : ""}`,
  );
  url.search = new URL(request.url).search;
  const headers = new Headers();
  for (const name of [
    "accept",
    "authorization",
    "content-type",
    "idempotency-key",
    "last-event-id",
  ]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  try {
    const upstream = await fetcher(url, {
      method: request.method,
      headers,
      body: await requestBody(request),
      cache: "no-store",
      signal: request.signal,
    });
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders(upstream),
    });
  } catch {
    return unavailableResponse();
  }
}

export async function proxyExternalAgentRequest(
  request: Request,
  config: HarnessServerConfig,
  prefix: "a2a/agent-triggers" | "chatops/agent-triggers",
  fetcher: typeof fetch = fetch,
  path = "",
) {
  const url = new URL(
    `${config.apiUrl}/${prefix}${path ? `/${path.replace(/^\//, "")}` : ""}`,
  );
  url.search = new URL(request.url).search;
  const headers = new Headers();
  for (const name of [
    "accept",
    "authorization",
    "content-type",
    "a2a-version",
    "last-event-id",
  ]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  try {
    const upstream = await fetcher(url, {
      method: request.method,
      headers,
      body: await requestBody(request),
      cache: "no-store",
      signal: request.signal,
    });
    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders(upstream),
    });
  } catch {
    return unavailableResponse();
  }
}

export async function proxyDataLifecycleRequest(
  request: Request,
  config: HarnessServerConfig,
  fetcher: typeof fetch = fetch,
  path = "",
) {
  const url = new URL(
    `${config.apiUrl}/v1/data-lifecycle${path ? `/${path.replace(/^\//, "")}` : ""}`,
  );
  url.search = new URL(request.url).search;
  return forward(
    request,
    url.toString(),
    config,
    fetcher,
    "harness.web.data_lifecycle",
  );
}

export async function proxyMemoryBankRequest(
  request: Request,
  config: HarnessServerConfig,
  fetcher: typeof fetch = fetch,
  path = "",
) {
  const url = new URL(
    `${config.apiUrl}/v1/memory-bank${path ? `/${path.replace(/^\//, "")}` : ""}`,
  );
  url.search = new URL(request.url).search;
  return forward(
    request,
    url.toString(),
    config,
    fetcher,
    "harness.web.memory_bank",
  );
}

export async function proxyRunRequest(request: Request, config: HarnessServerConfig, fetcher: typeof fetch = fetch, path = "") {
  const url = new URL(`${config.apiUrl}/v1/runs/${path}`);
  return forward(request, url.toString(), config, fetcher, "harness.web.run_control");
}
