import { downloadArtifact } from "../../../../../lib/harness-server";
import { inlineContentDisposition } from "../../../../../lib/content-disposition";

const FORWARDED_HEADERS = [
  "Content-Type",
  "Content-Length",
  "Content-Disposition",
] as const;

export async function GET(
  request: Request,
  context: { params: Promise<{ artifactId: string }> },
): Promise<Response> {
  const { artifactId } = await context.params;
  const upstream = await downloadArtifact(artifactId, request);
  const headers = new Headers();
  for (const name of FORWARDED_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) headers.set(name, value);
  }
  const setCookie = upstream.headers.get("set-cookie");
  if (setCookie) headers.set("Set-Cookie", setCookie);
  if (new URL(request.url).searchParams.get("preview") === "1") {
    headers.set(
      "Content-Disposition",
      inlineContentDisposition(headers.get("Content-Disposition")),
    );
    // The rail sandboxes generated pages in a frame; a preview opened in its
    // own tab has no frame around it, so the same restriction travels as a
    // header. Without it a generated page would run on the console origin and
    // could read the signed-in session.
    if ((headers.get("Content-Type") ?? "").toLowerCase().includes("text/html")) {
      headers.set(
        "Content-Security-Policy",
        "sandbox allow-scripts allow-popups allow-forms allow-modals",
      );
    }
  }
  return new Response(upstream.body, { status: upstream.status, headers });
}
