import {
  ACCESS_COOKIE, appendSessionCookies, readCookie, refreshSession,
} from "../../../../lib/auth-session";
import { getHarnessServerConfig } from "../../../../lib/server-config";

export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<Response> {
  const endpoint = process.env.KAI_DESKTOP_VIEWER_URL;
  if (!endpoint) return Response.json({ enabled: false }, { status: 404 });
  const config = getHarnessServerConfig();
  const search = new URL(request.url).search;
  let token = readCookie(request, ACCESS_COOKIE);
  let refreshed: Awaited<ReturnType<typeof refreshSession>>;
  try {
    if (!token) { refreshed = await refreshSession(request, config); token = refreshed?.access_token; }
    if (!token) return Response.json({ enabled: false }, { status: 401 });
    // The probe asks which generation this tab's own socket may type at; its
    // parameters must reach the viewer unchanged.
    const forward = (credential: string) => fetch(`${endpoint}/computer/status${search}`, {
      headers: { Authorization: `Bearer ${credential}` }, cache: "no-store", signal: AbortSignal.timeout(8000),
    });
    let upstream = await forward(token);
    if (upstream.status === 403 && !refreshed) {
      refreshed = await refreshSession(request, config);
      if (refreshed) upstream = await forward(refreshed.access_token);
    }
    const headers = new Headers({ "Content-Type": "application/json", "Cache-Control": "no-store" });
    if (refreshed) appendSessionCookies(headers, refreshed, config);
    return new Response(await upstream.text(), { status: upstream.status, headers });
  } catch { return Response.json({ enabled: false }, { status: 503 }); }
}
