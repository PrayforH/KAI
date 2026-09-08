import { getHarnessServerConfig } from "../../../../lib/server-config";

export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<Response> {
  const config = getHarnessServerConfig();
  const explicit = (process.env.HARNESS_PUBLIC_API_URL ?? "").replace(/\/$/, "");
  if (explicit) return Response.json({ baseUrl: `${explicit}/v1` });

  const publicOrigin = new URL(config.publicUrl || request.url);
  publicOrigin.pathname = "";
  publicOrigin.search = "";
  publicOrigin.hash = "";
  publicOrigin.port = process.env.HARNESS_API_PUBLIC_PORT ?? "8000";
  return Response.json({ baseUrl: `${publicOrigin.toString().replace(/\/$/, "")}/v1` });
}
