import { proxyStudioRequest } from "../../../../lib/harness-proxy";
import { getHarnessServerConfig } from "../../../../lib/server-config";

export const dynamic = "force-dynamic";

async function proxy(
  request: Request,
  context: { params: Promise<{ path: string[] }> },
) {
  const { path } = await context.params;
  return proxyStudioRequest(
    request,
    getHarnessServerConfig(),
    fetch,
    path.map(encodeURIComponent).join("/"),
  );
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
export const PATCH = proxy;
export const DELETE = proxy;
