import { authenticatedAuthProxy } from "../../../../../lib/auth-route";

export const dynamic = "force-dynamic";

export async function DELETE(
  request: Request,
  context: { params: Promise<{ keyId: string }> },
): Promise<Response> {
  const { keyId } = await context.params;
  return authenticatedAuthProxy(request, `api-keys/${encodeURIComponent(keyId)}`);
}
