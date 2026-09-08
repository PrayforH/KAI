import type { HarnessServerConfig } from "./server-config";

const REFRESH_RESULT_GRACE_MS = 3_000;

type RefreshFlight = {
  promise: Promise<AuthSessionPayload | undefined>;
  expiresAt: number;
};

// Access-token expiry can make several browser requests refresh at once. The
// upstream refresh token is single-use, so forwarding every request would make
// a legitimate concurrent request look like token reuse and revoke the whole
// session family. Coalesce the in-flight refresh and briefly share its result
// while the browser applies the replacement cookies.
const refreshFlights = new Map<string, RefreshFlight>();

export const ACCESS_COOKIE = "harness_access_token";
export const REFRESH_COOKIE = "harness_refresh_token";

export type AuthUser = {
  user_id: string;
  email: string;
  display_name: string;
  email_verified: boolean;
};

export type Membership = {
  tenant_id: string;
  user_id: string;
  role: "owner" | "admin" | "member" | "viewer";
};

export type AuthSessionPayload = {
  access_token: string;
  refresh_token: string;
  token_type: "bearer";
  expires_in: number;
  user: AuthUser;
  membership: Membership;
};

export function readCookie(request: Request, name: string): string | undefined {
  const cookie = request.headers.get("cookie") ?? "";
  for (const segment of cookie.split(";")) {
    const [key, ...parts] = segment.trim().split("=");
    if (key === name) return decodeURIComponent(parts.join("="));
  }
  return undefined;
}

function serializeCookie(
  name: string,
  value: string,
  maxAge: number,
  secure: boolean,
): string {
  return [
    `${name}=${encodeURIComponent(value)}`,
    "Path=/",
    "HttpOnly",
    "SameSite=Lax",
    `Max-Age=${Math.max(0, Math.floor(maxAge))}`,
    secure ? "Secure" : "",
  ]
    .filter(Boolean)
    .join("; ");
}

export function appendSessionCookies(
  headers: Headers,
  session: AuthSessionPayload,
  config: HarnessServerConfig,
): void {
  headers.append(
    "Set-Cookie",
    serializeCookie(
      ACCESS_COOKIE,
      session.access_token,
      session.expires_in,
      config.cookieSecure,
    ),
  );
  headers.append(
    "Set-Cookie",
    serializeCookie(
      REFRESH_COOKIE,
      session.refresh_token,
      config.refreshCookieDays * 86400,
      config.cookieSecure,
    ),
  );
}

export function appendClearedSessionCookies(
  headers: Headers,
  config: HarnessServerConfig,
): void {
  headers.append(
    "Set-Cookie",
    serializeCookie(ACCESS_COOKIE, "", 0, config.cookieSecure),
  );
  headers.append(
    "Set-Cookie",
    serializeCookie(REFRESH_COOKIE, "", 0, config.cookieSecure),
  );
}

export async function refreshSession(
  request: Request,
  config: HarnessServerConfig,
  fetcher: typeof fetch = fetch,
): Promise<AuthSessionPayload | undefined> {
  const refreshToken = readCookie(request, REFRESH_COOKIE);
  if (!refreshToken) return undefined;
  const now = Date.now();
  const existing = refreshFlights.get(refreshToken);
  if (existing && existing.expiresAt > now) return existing.promise;
  if (existing) refreshFlights.delete(refreshToken);

  const flight: RefreshFlight = {
    expiresAt: Number.POSITIVE_INFINITY,
    promise: (async () => {
      const response = await fetcher(`${config.apiUrl}/v1/auth/refresh`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh_token: refreshToken }),
        cache: "no-store",
      });
      if (!response.ok) return undefined;
      return (await response.json()) as AuthSessionPayload;
    })(),
  };
  refreshFlights.set(refreshToken, flight);
  void flight.promise.then((session) => {
    if (refreshFlights.get(refreshToken) !== flight) return;
    if (!session) {
      refreshFlights.delete(refreshToken);
      return;
    }
    flight.expiresAt = Date.now() + REFRESH_RESULT_GRACE_MS;
    globalThis.setTimeout(() => {
      if (refreshFlights.get(refreshToken) === flight) {
        refreshFlights.delete(refreshToken);
      }
    }, REFRESH_RESULT_GRACE_MS);
  }).catch(() => {
    if (refreshFlights.get(refreshToken) === flight) {
      refreshFlights.delete(refreshToken);
    }
  });
  return flight.promise;
}
