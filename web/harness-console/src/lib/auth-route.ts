import {
  ACCESS_COOKIE,
  type AuthSessionPayload,
  appendClearedSessionCookies,
  appendSessionCookies,
  readCookie,
  refreshSession,
} from "./auth-session";
import { getHarnessServerConfig } from "./server-config";

export async function credentialAuth(
  request: Request,
  action: "login" | "register",
): Promise<Response> {
  const config = getHarnessServerConfig();
  const upstream = await fetch(`${config.apiUrl}/v1/auth/${action}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: await request.text(),
    cache: "no-store",
  });
  const payload = await upstream.json();
  const headers = new Headers({ "Content-Type": "application/json" });
  if (!upstream.ok) {
    return Response.json(payload, { status: upstream.status, headers });
  }
  const session = payload as AuthSessionPayload;
  appendSessionCookies(headers, session, config);
  return Response.json(
    { user: session.user, membership: session.membership },
    { status: action === "register" ? 201 : 200, headers },
  );
}

export async function currentSession(request: Request): Promise<Response> {
  const config = getHarnessServerConfig();
  let accessToken = readCookie(request, ACCESS_COOKIE);
  let refreshed: AuthSessionPayload | undefined;
  if (!accessToken) {
    refreshed = await refreshSession(request, config);
    accessToken = refreshed?.access_token;
  }
  if (!accessToken) {
    const headers = new Headers();
    appendClearedSessionCookies(headers, config);
    return Response.json({ user: null }, { status: 401, headers });
  }
  let upstream = await fetch(`${config.apiUrl}/v1/auth/me`, {
    headers: { Authorization: `Bearer ${accessToken}` },
    cache: "no-store",
  });
  if (upstream.status === 401 && !refreshed) {
    refreshed = await refreshSession(request, config);
    if (refreshed) {
      upstream = await fetch(`${config.apiUrl}/v1/auth/me`, {
        headers: { Authorization: `Bearer ${refreshed.access_token}` },
        cache: "no-store",
      });
    }
  }
  const headers = new Headers({ "Content-Type": "application/json" });
  if (refreshed) appendSessionCookies(headers, refreshed, config);
  if (!upstream.ok) {
    const authError = upstream.headers.get("x-harness-auth-error");
    if (authError) headers.set("X-Harness-Auth-Error", authError);
    appendClearedSessionCookies(headers, config);
    return Response.json({ user: null }, { status: 401, headers });
  }
  return new Response(await upstream.text(), { status: 200, headers });
}

export async function authenticatedAuthMutation(
  request: Request,
  path: "me" | "password",
  options: { clearSessionOnSuccess?: boolean } = {},
): Promise<Response> {
  const config = getHarnessServerConfig();
  const body = await request.text();
  let accessToken = readCookie(request, ACCESS_COOKIE);
  let refreshed: AuthSessionPayload | undefined;
  if (!accessToken) {
    refreshed = await refreshSession(request, config);
    accessToken = refreshed?.access_token;
  }
  const headers = new Headers();
  if (!accessToken) {
    appendClearedSessionCookies(headers, config);
    return Response.json(
      { error: { code: "auth_required", message: "登录状态已失效，请重新登录。" } },
      { status: 401, headers },
    );
  }
  const forward = (token: string) =>
    fetch(`${config.apiUrl}/v1/auth/${path}`, {
      method: request.method,
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
      },
      body,
      cache: "no-store",
    });
  let upstream = await forward(accessToken);
  if (upstream.status === 401 && !refreshed) {
    refreshed = await refreshSession(request, config);
    if (refreshed) upstream = await forward(refreshed.access_token);
  }
  const contentType = upstream.headers.get("content-type");
  if (contentType) headers.set("Content-Type", contentType);
  if (upstream.ok && options.clearSessionOnSuccess) {
    appendClearedSessionCookies(headers, config);
  } else if (refreshed) {
    appendSessionCookies(headers, refreshed, config);
  } else if (upstream.status === 401) {
    appendClearedSessionCookies(headers, config);
  }
  const responseBody = upstream.status === 204 ? null : await upstream.text();
  return new Response(responseBody, { status: upstream.status, headers });
}

export async function authenticatedAuthProxy(
  request: Request,
  path: string,
): Promise<Response> {
  const config = getHarnessServerConfig();
  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  const body = hasBody ? await request.text() : undefined;
  let accessToken = readCookie(request, ACCESS_COOKIE);
  let refreshed: AuthSessionPayload | undefined;
  if (!accessToken) {
    refreshed = await refreshSession(request, config);
    accessToken = refreshed?.access_token;
  }
  const headers = new Headers();
  if (!accessToken) {
    appendClearedSessionCookies(headers, config);
    return Response.json(
      { error: { code: "auth_required", message: "登录状态已失效，请重新登录。" } },
      { status: 401, headers },
    );
  }
  const forward = (token: string) =>
    fetch(`${config.apiUrl}/v1/auth/${path}`, {
      method: request.method,
      headers: {
        Authorization: `Bearer ${token}`,
        ...(hasBody ? { "Content-Type": "application/json" } : {}),
      },
      body,
      cache: "no-store",
    });
  let upstream = await forward(accessToken);
  if (upstream.status === 401 && !refreshed) {
    refreshed = await refreshSession(request, config);
    if (refreshed) upstream = await forward(refreshed.access_token);
  }
  const contentType = upstream.headers.get("content-type");
  if (contentType) headers.set("Content-Type", contentType);
  if (refreshed) {
    appendSessionCookies(headers, refreshed, config);
  } else if (upstream.status === 401) {
    appendClearedSessionCookies(headers, config);
  }
  return new Response(await upstream.text(), {
    status: upstream.status,
    headers,
  });
}

export async function logoutSession(request: Request): Promise<Response> {
  const config = getHarnessServerConfig();
  const refreshToken = readCookie(request, "harness_refresh_token");
  if (refreshToken) {
    await fetch(`${config.apiUrl}/v1/auth/logout`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refreshToken }),
      cache: "no-store",
    }).catch(() => undefined);
  }
  const headers = new Headers();
  appendClearedSessionCookies(headers, config);
  headers.set("Location", "/login");
  return new Response(null, { status: 303, headers });
}
