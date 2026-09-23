import { proxyInputArtifactRequest } from "../../../../lib/harness-proxy";
import { getHarnessServerConfig } from "../../../../lib/server-config";

export const dynamic = "force-dynamic";

export async function GET(request: Request) {
  return proxyInputArtifactRequest(request, getHarnessServerConfig(), fetch, "/limits");
}
