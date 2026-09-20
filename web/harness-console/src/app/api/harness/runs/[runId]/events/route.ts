import { proxyRunRequest } from "../../../../../../lib/harness-proxy";
import { getHarnessServerConfig } from "../../../../../../lib/server-config";
export const dynamic = "force-dynamic";
export async function GET(request: Request, context: { params: Promise<{ runId: string }> }) {
  const { runId } = await context.params;
  return proxyRunRequest(request, getHarnessServerConfig(), fetch, `${encodeURIComponent(runId)}/events`);
}
