import {
  getHarnessServerConfig,
  type ServerEnvironment,
} from "./server-config";
import {
  ACCESS_COOKIE,
  appendClearedSessionCookies,
  appendSessionCookies,
  readCookie,
  refreshSession,
} from "./auth-session";

export type ApprovalDecision = "approved" | "rejected";

export function decideApproval(
  approvalId: string,
  decision: ApprovalDecision,
  request: Request,
  fetcher: typeof fetch = fetch,
  environment: ServerEnvironment = process.env,
): Promise<Response> {
  const config = getHarnessServerConfig(environment);
  return authenticatedFetch(
    request,
    `${config.apiUrl}/v1/approvals/${encodeURIComponent(approvalId)}`,
    {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ decision }),
    },
    config,
    fetcher,
  );
}

export function downloadArtifact(
  artifactId: string,
  request: Request,
  fetcher: typeof fetch = fetch,
  environment: ServerEnvironment = process.env,
): Promise<Response> {
  const config = getHarnessServerConfig(environment);
  const url = new URL(`${config.apiUrl}/v1/artifacts/${encodeURIComponent(artifactId)}/content`);
  const threadId = new URL(request.url).searchParams.get("thread_id");
  if (threadId) url.searchParams.set("thread_id", threadId);
  return authenticatedFetch(
    request,
    url.toString(),
    {},
    config,
    fetcher,
  );
}

export function listArtifacts(
  request: Request,
  fetcher: typeof fetch = fetch,
  environment: ServerEnvironment = process.env,
): Promise<Response> {
  const config = getHarnessServerConfig(environment);
  const url = new URL(`${config.apiUrl}/v1/artifacts`);
  url.search = new URL(request.url).search;
  return authenticatedFetch(request, url.toString(), {}, config, fetcher);
}

export function downloadInputArtifact(
  inputArtifactId: string,
  request: Request,
  fetcher: typeof fetch = fetch,
  environment: ServerEnvironment = process.env,
): Promise<Response> {
  const config = getHarnessServerConfig(environment);
  return authenticatedFetch(
    request,
    `${config.apiUrl}/v1/input-artifacts/${encodeURIComponent(inputArtifactId)}/content`,
    {},
    config,
    fetcher,
  );
}

async function authenticatedFetch(
  request: Request,
  url: string,
  init: RequestInit,
  config: ReturnType<typeof getHarnessServerConfig>,
  fetcher: typeof fetch,
): Promise<Response> {
  const accessToken = readCookie(request, ACCESS_COOKIE);
  const makeHeaders = (token?: string) => ({
    ...Object.fromEntries(new Headers(init.headers).entries()),
    ...config.serviceHeaders,
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  });
  let upstream = await fetcher(url, {
    ...init,
    headers: makeHeaders(accessToken),
    cache: "no-store",
  });
  let refreshed;
  if (upstream.status === 401) {
    refreshed = await refreshSession(request, config, fetcher);
    if (refreshed) {
      upstream = await fetcher(url, {
        ...init,
        headers: makeHeaders(refreshed.access_token),
        cache: "no-store",
      });
    }
  }
  const headers = new Headers(upstream.headers);
  if (refreshed) appendSessionCookies(headers, refreshed, config);
  else if (upstream.status === 401) appendClearedSessionCookies(headers, config);
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers,
  });
}
