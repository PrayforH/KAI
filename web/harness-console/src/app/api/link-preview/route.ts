import { currentSession } from "../../../lib/auth-route";
import { fetchLinkPreview } from "../../../lib/link-preview-server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
export async function GET(request: Request): Promise<Response> {
  const session = await currentSession(request);
  if (!session.ok) return session;
  const headers = new Headers({ "Cache-Control": "private, no-store" });
  for (const value of session.headers.getSetCookie()) headers.append("Set-Cookie", value);
  try {
    const preview = await fetchLinkPreview(new URL(request.url).searchParams.get("url") ?? "");
    return Response.json({ preview }, { headers });
  } catch { return Response.json({ preview: null }, { status: 400, headers }); }
}
