import { authenticatedAuthProxy } from "../../../../lib/auth-route";

export const dynamic = "force-dynamic";

export async function GET(request: Request): Promise<Response> {
  return authenticatedAuthProxy(request, "api-keys");
}

export async function POST(request: Request): Promise<Response> {
  return authenticatedAuthProxy(request, "api-keys");
}
