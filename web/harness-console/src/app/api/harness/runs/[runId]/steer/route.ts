import { proxyRunRequest } from "../../../../../../lib/harness-proxy";
import { getHarnessServerConfig } from "../../../../../../lib/server-config";
export const dynamic = "force-dynamic";
async function proxy(request: Request, context: { params: Promise<{ runId: string }> }) {
  const { runId } = await context.params;
  return proxyRunRequest(request, getHarnessServerConfig(), fetch, `${encodeURIComponent(runId)}/steer`);
}
export const GET = proxy;
export const POST = proxy;
