import { describe, expect, it } from "vitest";
import {
  proxyAgentCatalogRequest,
  proxyAgentTriggerRequest,
  proxyAguiRequest,
  proxyDataLifecycleRequest,
  proxyExternalAgentRequest,
  proxyInputArtifactRequest,
  proxyMemoryBankRequest,
  proxyStudioRequest,
} from "../src/lib/harness-proxy";
import type { HarnessServerConfig } from "../src/lib/server-config";

const config: HarnessServerConfig = {
  apiUrl: "http://harness.internal:8000",
  agentName: "echo-agent",
  agentVersion: "0.1.0",
  aguiUrl:
    "http://harness.internal:8000/v1/agui?agent_name=echo-agent&agent_version=0.1.0",
  serviceHeaders: {
    "X-Harness-Service-Token": "server-only-token",
  },
  cookieSecure: false,
  refreshCookieDays: 30,
  googleClientId: "",
  githubClientId: "",
  publicUrl: "",
};

describe("Harness same-origin proxies", () => {
  it("injects server-only identity and preserves the AG-UI response stream", async () => {
    let upstreamUrl = "";
    let upstreamInit: RequestInit | undefined;
    const fetcher: typeof fetch = async (input, init) => {
      upstreamUrl = String(input);
      upstreamInit = init;
      const encoder = new TextEncoder();
      return new Response(
        new ReadableStream({
          start(controller) {
            controller.enqueue(encoder.encode("data: first\n\n"));
            controller.enqueue(encoder.encode("data: second\n\n"));
            controller.close();
          },
        }),
        {
          status: 200,
          headers: {
            "Content-Type": "text/event-stream",
            "X-Harness-Run-ID": "server-run-1",
          },
        },
      );
    };
    const request = new Request("http://console.test/api/agui", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Cookie: "private=browser; harness_access_token=user-jwt",
      },
      body: JSON.stringify({ threadId: "thread-1" }),
    });

    const response = await proxyAguiRequest(request, config, fetcher);

    expect(upstreamUrl).toBe(config.aguiUrl);
    const headers = new Headers(upstreamInit?.headers);
    expect(headers.get("X-Tenant-ID")).toBeNull();
    expect(headers.get("X-User-ID")).toBeNull();
    expect(headers.get("Authorization")).toBe("Bearer user-jwt");
    expect(headers.get("X-Harness-Service-Token")).toBe("server-only-token");
    expect(headers.get("Cookie")).toBeNull();
    expect(headers.get("Content-Type")).toBe("application/json");
    expect(response.headers.get("Content-Type")).toBe("text/event-stream");
    expect(response.headers.get("X-Harness-Run-ID")).toBe("server-run-1");
    expect(await response.text()).toBe("data: first\n\ndata: second\n\n");
  });

  it("forwards each AG-UI chunk before the upstream stream closes", async () => {
    const encoder = new TextEncoder();
    let controller: ReadableStreamDefaultController<Uint8Array> | undefined;
    const response = await proxyAguiRequest(
      new Request("http://console.test/api/agui", {
        method: "POST",
        body: "{}",
      }),
      config,
      async () =>
        new Response(
          new ReadableStream<Uint8Array>({
            start(nextController) {
              controller = nextController;
              nextController.enqueue(encoder.encode("data: first\n\n"));
            },
          }),
          {
            status: 200,
            headers: {
              "Cache-Control": "no-cache",
              "Content-Type": "text/event-stream",
              "X-Accel-Buffering": "no",
            },
          },
        ),
    );
    const reader = response.body?.getReader();

    expect(reader).toBeDefined();
    const first = await reader!.read();
    expect(new TextDecoder().decode(first.value)).toBe("data: first\n\n");
    expect(first.done).toBe(false);

    controller?.enqueue(encoder.encode("data: second\n\n"));
    controller?.close();
    const second = await reader!.read();

    expect(new TextDecoder().decode(second.value)).toBe("data: second\n\n");
    expect(second.done).toBe(false);
  });

  it("continues W3C trace context across the Web-to-API boundary", async () => {
    const traceparent =
      "00-1234567890abcdef1234567890abcdef-1234567890abcdef-01";
    let upstreamHeaders = new Headers();
    const request = new Request("http://console.test/api/agui", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        traceparent,
        tracestate: "vendor=value",
      },
      body: "{}",
    });

    const response = await proxyAguiRequest(
      request,
      config,
      async (_input, init) => {
        upstreamHeaders = new Headers(init?.headers);
        return new Response("done", { status: 200 });
      },
    );

    expect(upstreamHeaders.get("traceparent")).toBe(traceparent);
    expect(upstreamHeaders.get("tracestate")).toBe("vendor=value");
    expect(await response.text()).toBe("done");
  });

  it("routes cancellation through the same identity boundary", async () => {
    let upstreamUrl = "";
    const fetcher: typeof fetch = async (input) => {
      upstreamUrl = String(input);
      return new Response(null, { status: 200 });
    };
    const request = new Request(
      "http://console.test/api/agui/threads/thread%2F1/runs/run%2F1/cancel",
      { method: "POST", headers: { Cookie: "harness_access_token=user-jwt" } },
    );

    await proxyAguiRequest(
      request,
      config,
      fetcher,
      "threads/thread%2F1/runs/run%2F1/cancel",
    );

    expect(upstreamUrl).toBe(
      "http://harness.internal:8000/v1/agui/threads/thread%2F1/runs/run%2F1/cancel",
    );
  });

  it("preserves thread-list filters across the same-origin proxy", async () => {
    let upstreamUrl = "";
    await proxyAguiRequest(
      new Request("http://console.test/api/agui/threads?archived=true"),
      config,
      async (input) => {
        upstreamUrl = String(input);
        return new Response("[]", { status: 200 });
      },
      "threads",
    );

    expect(upstreamUrl).toBe(
      "http://harness.internal:8000/v1/agui/threads?archived=true",
    );
  });

  it("forwards a validated per-thread agent coordinate for task switching", async () => {
    let upstreamUrl = "";
    await proxyAguiRequest(
      new Request(
        "http://console.test/api/agui?agent_name=public-opinion-agent&agent_version=0.2.0",
        { method: "POST", body: "{}" },
      ),
      config,
      async (input) => {
        upstreamUrl = String(input);
        return new Response(null, { status: 200 });
      },
    );

    expect(upstreamUrl).toBe(
      "http://harness.internal:8000/v1/agui?agent_name=public-opinion-agent&agent_version=0.2.0",
    );
  });

  it("loads the published runtime catalog through the authenticated BFF", async () => {
    let upstreamUrl = "";
    const response = await proxyAgentCatalogRequest(
      new Request("http://console.test/api/harness/agents", {
        headers: { Cookie: "harness_access_token=user-jwt" },
      }),
      config,
      async (input, init) => {
        upstreamUrl = String(input);
        expect(new Headers(init?.headers).get("Authorization")).toBe(
          "Bearer user-jwt",
        );
        return Response.json([
          {
            name: "public-opinion-agent",
            version: "0.1.1",
            display_name: "public-opinion-agent",
            domain: "public-opinion",
          },
        ]);
      },
    );

    expect(upstreamUrl).toBe("http://harness.internal:8000/v1/agents");
    expect(await response.json()).toHaveLength(1);
  });

  it("forwards personal version history and promotion under the agent identity", async () => {
    let upstreamUrl = "";
    let upstreamMethod = "";
    await proxyAgentCatalogRequest(
      new Request(
        "http://console.test/api/harness/agents/agent-one/versions/0.1.0/promote",
        { method: "POST", headers: { Cookie: "harness_access_token=user-jwt" } },
      ),
      config,
      async (input, init) => {
        upstreamUrl = String(input);
        upstreamMethod = init?.method ?? "";
        return Response.json({ current_version: "0.1.0" });
      },
      "agent-one/versions/0.1.0/promote",
    );

    expect(upstreamUrl).toBe(
      "http://harness.internal:8000/v1/agents/agent-one/versions/0.1.0/promote",
    );
    expect(upstreamMethod).toBe("POST");
  });

  it("forwards multipart bytes without exposing internal identity in the response", async () => {
    let upstreamInit: RequestInit | undefined;
    const fetcher: typeof fetch = async (_input, init) => {
      upstreamInit = init;
      return Response.json(
        { input_artifact_id: "input_artifact_1" },
        { status: 201 },
      );
    };
    const request = new Request("http://console.test/api/input-artifacts", {
      method: "POST",
      headers: {
        "Content-Type": "multipart/form-data; boundary=test",
        Cookie: "harness_access_token=user-jwt",
      },
      body: "--test--",
    });

    const response = await proxyInputArtifactRequest(request, config, fetcher);

    const headers = new Headers(upstreamInit?.headers);
    expect(headers.get("Content-Type")).toContain("boundary=test");
    expect(headers.get("X-Tenant-ID")).toBeNull();
    expect(headers.get("Authorization")).toBe("Bearer user-jwt");
    expect(await response.json()).toEqual({
      input_artifact_id: "input_artifact_1",
    });
    expect(response.headers.get("X-Tenant-ID")).toBeNull();
  });

  it("returns a generic 502 when the internal Harness endpoint is unavailable", async () => {
    const fetcher: typeof fetch = async () => {
      throw new Error("connect ECONNREFUSED http://harness.internal:8000");
    };

    const response = await proxyAguiRequest(
      new Request("http://console.test/api/agui", {
        method: "POST",
        body: "{}",
      }),
      config,
      fetcher,
    );

    expect(response.status).toBe(502);
    expect(await response.text()).not.toContain("harness.internal");
  });

  it("proxies Studio mutations and bundle headers through the authenticated BFF", async () => {
    let upstreamUrl = "";
    let upstreamInit: RequestInit | undefined;
    const fetcher: typeof fetch = async (input, init) => {
      upstreamUrl = String(input);
      upstreamInit = init;
      return new Response("bundle", {
        status: 200,
        headers: {
          "Content-Type": "application/zip",
          "Content-Disposition": 'attachment; filename="agent-0.1.0.zip"',
          ETag: '"archive-hash"',
          "X-Agent-Package-SHA256": "package-hash",
        },
      });
    };
    const request = new Request(
      "http://console.test/api/studio/drafts/draft-1/bundle?download=true",
      { headers: { Cookie: "harness_access_token=user-jwt" } },
    );

    const response = await proxyStudioRequest(
      request,
      config,
      fetcher,
      "drafts/draft-1/bundle",
    );

    expect(upstreamUrl).toBe(
      "http://harness.internal:8000/v1/studio/drafts/draft-1/bundle?download=true",
    );
    const headers = new Headers(upstreamInit?.headers);
    expect(headers.get("Authorization")).toBe("Bearer user-jwt");
    expect(headers.get("X-Harness-Service-Token")).toBe("server-only-token");
    expect(response.headers.get("Content-Disposition")).toContain("agent-0.1.0.zip");
    expect(response.headers.get("ETag")).toBe('"archive-hash"');
    expect(response.headers.get("X-Agent-Package-SHA256")).toBe("package-hash");
  });

  it("forwards only the external trigger secret and idempotency key", async () => {
    let upstreamUrl = "";
    let upstreamHeaders = new Headers();
    const response = await proxyAgentTriggerRequest(
      new Request("http://console.test/webhooks/agent-triggers/trigger-1", {
        method: "POST",
        headers: {
          Authorization: "Bearer trigger-secret",
          "Idempotency-Key": "event-42",
          "Content-Type": "application/json",
          Cookie: "harness_access_token=user-jwt",
        },
        body: JSON.stringify({ prompt: "处理事件" }),
      }),
      config,
      async (input, init) => {
        upstreamUrl = String(input);
        upstreamHeaders = new Headers(init?.headers);
        return Response.json({ runId: "run-1" }, { status: 202 });
      },
      "trigger-1",
    );

    expect(upstreamUrl).toBe(
      "http://harness.internal:8000/webhooks/agent-triggers/trigger-1",
    );
    expect(upstreamHeaders.get("Authorization")).toBe("Bearer trigger-secret");
    expect(upstreamHeaders.get("Idempotency-Key")).toBe("event-42");
    expect(upstreamHeaders.get("X-Harness-Service-Token")).toBeNull();
    expect(upstreamHeaders.get("Cookie")).toBeNull();
    expect(response.status).toBe(202);
  });

  it("preserves external protocol cursors, query parameters, and auth challenges", async () => {
    let upstreamUrl = "";
    let upstreamHeaders = new Headers();
    const response = await proxyExternalAgentRequest(
      new Request(
        "http://console.test/a2a/agent-triggers/trigger-1/tasks?pageSize=10&pageToken=next",
        {
          headers: {
            Authorization: "Bearer trigger-secret",
            "A2A-Version": "1.0",
            "Last-Event-ID": "7",
          },
        },
      ),
      config,
      "a2a/agent-triggers",
      async (input, init) => {
        upstreamUrl = String(input);
        upstreamHeaders = new Headers(init?.headers);
        return Response.json(
          { error: { status: "UNAUTHENTICATED" } },
          { status: 401, headers: { "WWW-Authenticate": "Bearer" } },
        );
      },
      "trigger-1/tasks",
    );

    expect(upstreamUrl).toBe(
      "http://harness.internal:8000/a2a/agent-triggers/trigger-1/tasks?pageSize=10&pageToken=next",
    );
    expect(upstreamHeaders.get("A2A-Version")).toBe("1.0");
    expect(upstreamHeaders.get("Last-Event-ID")).toBe("7");
    expect(response.headers.get("WWW-Authenticate")).toBe("Bearer");
  });

  it("preserves lifecycle export filenames through the authenticated BFF", async () => {
    let upstreamUrl = "";
    const response = await proxyDataLifecycleRequest(
      new Request("http://console.test/api/data-lifecycle/jobs/job-1/artifact", {
        headers: { Cookie: "harness_access_token=user-jwt" },
      }),
      config,
      async (input) => {
        upstreamUrl = String(input);
        return new Response("zip", {
          headers: {
            "Content-Type": "application/zip",
            "Content-Disposition": "attachment; filename*=UTF-8''data-export.zip",
          },
        });
      },
      "jobs/job-1/artifact",
    );
    expect(upstreamUrl).toBe(
      "http://harness.internal:8000/v1/data-lifecycle/jobs/job-1/artifact",
    );
    expect(response.headers.get("Content-Disposition")).toContain("data-export.zip");
  });

  it("keeps memory scope and optimistic version behind the authenticated BFF", async () => {
    let upstreamUrl = "";
    let upstreamInit: RequestInit | undefined;
    await proxyMemoryBankRequest(
      new Request("http://console.test/api/memory-bank/entries/memory-1", {
        method: "PUT",
        headers: {
          Cookie: "harness_access_token=user-jwt",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ expectedVersion: 4, content: "偏好中文" }),
      }),
      config,
      async (input, init) => {
        upstreamUrl = String(input);
        upstreamInit = init;
        return Response.json({ version: 5 });
      },
      "entries/memory-1",
    );
    expect(upstreamUrl).toBe(
      "http://harness.internal:8000/v1/memory-bank/entries/memory-1",
    );
    expect(new Headers(upstreamInit?.headers).get("Authorization")).toBe(
      "Bearer user-jwt",
    );
    expect(JSON.parse(new TextDecoder().decode(upstreamInit?.body as ArrayBuffer))).toEqual({
      expectedVersion: 4,
      content: "偏好中文",
    });
  });
});
