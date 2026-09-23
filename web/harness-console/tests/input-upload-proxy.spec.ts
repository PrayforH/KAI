import { describe, expect, it } from "vitest";
import { proxyInputArtifactRequest } from "../src/lib/harness-proxy";
import { getHarnessServerConfig } from "../src/lib/server-config";
const config = getHarnessServerConfig({ HARNESS_API_URL: "http://api.test" });
const limits = () => Response.json({ max_file_bytes: 25 * 1024 * 1024 });
function streamingRequest(cookie = "harness_access_token=valid", size = "20") {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({ start(value) { controller = value; } });
  const init: RequestInit & { duplex: "half" } = {
    method: "POST", body: stream, duplex: "half",
    headers: { Cookie: cookie, "content-type": "multipart/form-data; boundary=test", "x-upload-size": size },
  };
  return { request: new Request("http://web.test/api/input-artifacts", init), controller };
}
describe("streaming input uploads", () => {
  it("starts forwarding before the last browser byte arrives", async () => {
    const { request, controller } = streamingRequest();
    const response = await proxyInputArtifactRequest(request, config, async (url, init) => {
      if (String(url).endsWith("/limits")) return limits();
      expect(init?.body).toBe(request.body);
      expect((init as RequestInit & { duplex: string }).duplex).toBe("half");
      controller.enqueue(new TextEncoder().encode("--test--")); controller.close();
      expect(await new Response(init?.body).text()).toBe("--test--");
      return Response.json({ status: "ready" }, { status: 201 });
    });
    expect(response.status).toBe(201); await response.text();
  }, 1000);
  it("refreshes three concurrent uploads before consuming their bodies", async () => {
    let refreshes = 0; let uploads = 0;
    const requests = Array.from({ length: 3 }, () => streamingRequest("harness_access_token=expired; harness_refresh_token=upload-concurrent-test"));
    const fetcher: typeof fetch = async (url, init) => {
      const token = new Headers(init?.headers).get("authorization");
      if (String(url).endsWith("/refresh")) {
        refreshes++;
        expect(requests.every(({ request }) => !request.bodyUsed)).toBe(true);
        return Response.json({ access_token: "new-access", refresh_token: "new-refresh", expires_in: 1800 });
      }
      if (String(url).endsWith("/limits")) return token === "Bearer new-access" ? limits() : new Response(null, { status: 401 });
      expect(token).toBe("Bearer new-access");
      const item = requests.find(({ request }) => request.body === init?.body)!;
      item.controller.enqueue(new Uint8Array([++uploads])); item.controller.close();
      expect((await new Response(init?.body).arrayBuffer()).byteLength).toBe(1);
      return Response.json({ status: "ready" }, { status: 201 });
    };
    const responses = await Promise.all(requests.map(({ request }) => proxyInputArtifactRequest(request, config, fetcher)));
    expect(refreshes).toBe(1); expect(uploads).toBe(3);
    for (const response of responses) {
      expect(response.status).toBe(201);
      expect(response.headers.get("set-cookie")).toContain("new-access"); await response.text();
    }
  }, 1000);
  it("rejects oversized files without reading or forwarding contents", async () => {
    const { request, controller } = streamingRequest(undefined, String(26 * 1024 * 1024));
    const calls: string[] = [];
    const response = await proxyInputArtifactRequest(request, config, async (url) => { calls.push(String(url)); return limits(); });
    expect(response.status).toBe(413);
    expect((await response.json()).error.message).toContain("25 MB");
    expect(request.bodyUsed).toBe(false);
    expect(calls).toEqual(["http://api.test/v1/input-artifacts/limits"]); controller.close();
  });
  it("does not transmit a body when authentication fails", async () => {
    const { request, controller } = streamingRequest();
    const response = await proxyInputArtifactRequest(request, config, async (url) => {
      expect(String(url)).toContain("/limits");
      return Response.json({ error: { message: "expired" } }, { status: 401 });
    });
    expect(response.status).toBe(401); expect(request.bodyUsed).toBe(false);
    await response.text(); controller.close();
  });
  it("does not replay a transmitted upload after an upstream failure", async () => {
    const { request, controller } = streamingRequest(); let uploads = 0;
    const response = await proxyInputArtifactRequest(request, config, async (url, init) => {
      if (String(url).endsWith("/limits")) return limits();
      uploads++; controller.enqueue(new Uint8Array([1])); controller.close();
      await new Response(init?.body).arrayBuffer(); return new Response(null, { status: 507 });
    });
    expect(response.status).toBe(507); expect(uploads).toBe(1);
  });
});
