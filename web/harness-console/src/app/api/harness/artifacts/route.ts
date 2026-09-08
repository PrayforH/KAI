import { listArtifacts } from "../../../../lib/harness-server";

export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<Response> {
  return listArtifacts(request);
}
