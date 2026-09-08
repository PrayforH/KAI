import { afterEach, describe, expect, it, vi } from "vitest";
import {
  authenticatedAuthMutation,
  authenticatedAuthProxy,
  currentSession,
} from "../src/lib/auth-route";
import { refreshSession } from "../src/lib/auth-session";

afterEach(() => {
  vi.unstubAllEnvs();
  vi.unstubAllGlobals();
});

describe("authenticated workspace member requests", () => {
  it("forwards member listing and role updates through the user session", async () => {
    vi.stubEnv("HARNESS_API_URL", "http://harness.internal:8000");
    const calls: Array<{ url: string; method: string; body?: string }> = [];
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({
        url: String(input),
        method: String(init?.method),
        body: typeof init?.body === "string" ? init.body : undefined,
      });
      return Response.json([]);
    });

    await authenticatedAuthProxy(
      new Request("http://console.test/api/auth/members", {
        headers: { Cookie: "harness_access_token=user-jwt" },
      }),
      "members",
    );
    await authenticatedAuthProxy(
      new Request("http://console.test/api/auth/members/user-2", {
        method: "PATCH",
        headers: { Cookie: "harness_access_token=user-jwt" },
        body: JSON.stringify({ role: "admin" }),
      }),
      "members/user-2",
    );

    expect(calls).toEqual([
      {
        url: "http://harness.internal:8000/v1/auth/members",
        method: "GET",
      },
      {
        url: "http://harness.internal:8000/v1/auth/members/user-2",
        method: "PATCH",
        body: '{"role":"admin"}',
      },
    ]);
  });
});

describe("current browser session", () => {
  it("coalesces concurrent refreshes of the same rotating token", async () => {
    const payload = {
      access_token: "replacement-access",
      refresh_token: "replacement-refresh",
      token_type: "bearer" as const,
      expires_in: 1_800,
      user: {
        user_id: "user-a",
        email: "user@example.com",
        display_name: "User A",
        email_verified: true,
      },
      membership: {
        tenant_id: "tenant-a",
        user_id: "user-a",
        role: "owner" as const,
      },
    };
    const fetcher = vi.fn(async () => Response.json(payload));
    const request = new Request("http://console.test/api/auth/session", {
      headers: { Cookie: "harness_refresh_token=rotating-token" },
    });
    const config = {
      apiUrl: "http://harness.internal:8000",
      agentName: "lead-agent",
      agentVersion: "1.0.0",
      aguiUrl: "http://harness.internal:8000/v1/agui",
      serviceHeaders: {},
      cookieSecure: false,
      refreshCookieDays: 30,
      googleClientId: "",
      githubClientId: "",
      publicUrl: "",
    };

    const [first, second] = await Promise.all([
      refreshSession(request, config, fetcher),
      refreshSession(request, config, fetcher),
    ]);
    const duringCookieUpdate = await refreshSession(request, config, fetcher);

    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(first).toEqual(payload);
    expect(second).toEqual(payload);
    expect(duringCookieUpdate).toEqual(payload);
  });

  it("preserves the replaced-session reason while clearing stale cookies", async () => {
    vi.stubEnv("HARNESS_API_URL", "http://harness.internal:8000");
    vi.stubGlobal("fetch", async (input: RequestInfo | URL) => {
      if (String(input).endsWith("/v1/auth/refresh")) {
        return Response.json({ error: { code: "refresh_invalid" } }, { status: 401 });
      }
      return Response.json(
        { error: { code: "session_replaced" } },
        {
          status: 401,
          headers: { "X-Harness-Auth-Error": "session_replaced" },
        },
      );
    });

    const response = await currentSession(
      new Request("http://console.test/api/auth/session", {
        headers: {
          Cookie: "harness_access_token=old-jwt; harness_refresh_token=old-refresh",
        },
      }),
    );

    expect(response.status).toBe(401);
    expect(response.headers.get("x-harness-auth-error")).toBe("session_replaced");
    expect(response.headers.get("set-cookie")).toContain("Max-Age=0");
  });
});

describe("authenticated account mutations", () => {
  it("forwards profile updates with the signed user token", async () => {
    vi.stubEnv("HARNESS_API_URL", "http://harness.internal:8000");
    let receivedUrl = "";
    let receivedInit: RequestInit | undefined;
    vi.stubGlobal("fetch", async (input: RequestInfo | URL, init?: RequestInit) => {
      receivedUrl = String(input);
      receivedInit = init;
      return Response.json({ display_name: "New Name" });
    });

    const response = await authenticatedAuthMutation(
      new Request("http://console.test/api/auth/profile", {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          Cookie: "harness_access_token=user-jwt",
        },
        body: JSON.stringify({ display_name: "New Name" }),
      }),
      "me",
    );

    expect(receivedUrl).toBe("http://harness.internal:8000/v1/auth/me");
    expect(receivedInit?.method).toBe("PATCH");
    expect(new Headers(receivedInit?.headers).get("Authorization")).toBe(
      "Bearer user-jwt",
    );
    expect(await response.json()).toEqual({ display_name: "New Name" });
  });

  it("clears browser credentials after a successful password change", async () => {
    vi.stubEnv("HARNESS_API_URL", "http://harness.internal:8000");
    vi.stubGlobal("fetch", async () => new Response(null, { status: 204 }));

    const response = await authenticatedAuthMutation(
      new Request("http://console.test/api/auth/password", {
        method: "POST",
        headers: { Cookie: "harness_access_token=user-jwt" },
        body: JSON.stringify({
          current_password: "CurrentPass123",
          new_password: "NewSecurePass456",
        }),
      }),
      "password",
      { clearSessionOnSuccess: true },
    );

    expect(response.status).toBe(204);
    const cookies = response.headers.get("set-cookie") ?? "";
    expect(cookies).toContain("harness_access_token=");
    expect(cookies).toContain("harness_refresh_token=");
    expect(cookies).toContain("Max-Age=0");
  });
});
