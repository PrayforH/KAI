import { afterEach, expect, it, vi } from "vitest";
import { proxyDictationRequest } from "../src/lib/harness-proxy";
import type { HarnessServerConfig } from "../src/lib/server-config";

const config: HarnessServerConfig = {
  apiUrl: "http://api.test:8000", agentName: "lead-agent", agentVersion: "1.0.1",
  aguiUrl: "http://api.test:8000/v1/agui", serviceHeaders: {}, cookieSecure: false,
  refreshCookieDays: 30, googleClientId: "", githubClientId: "", publicUrl: "",
};
afterEach(() => vi.unstubAllGlobals());

it.each(["vad", "transcribe"])("forwards a valid multi-second WAV upload to %s", async path => {
  const fetcher = vi.fn(async (_url, init: RequestInit) => {
    expect((init.body as ArrayBuffer).byteLength).toBe(200_000);
    return Response.json({ text: "语音输入测试" });
  });
  vi.stubGlobal("fetch", fetcher);
  const response = await proxyDictationRequest(new Request("http://web.test/dictation", {
    method: "POST", body: new Uint8Array(200_000),
  }), config, path);
  expect(response.status).toBe(200);
  expect(fetcher).toHaveBeenCalledOnce();
});

it.each([["sessions/id/audio", 32_001], ["transcribe", 300 * 32000 + 65537], ["refine", 64_001]] as const)(
  "rejects oversized %s before contacting the API", async (path, size) => {
    const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
    const response = await proxyDictationRequest(new Request("http://web.test/dictation", {
      method: "POST", body: new Uint8Array(size),
    }), config, path);
    expect(response.status).toBe(413);
    expect(fetcher).not.toHaveBeenCalled();
  },
);

it("forwards a two-minute recording beyond the old fragment limit", async () => {
  const fetcher = vi.fn(async (_url, init: RequestInit) => {
    expect((init.body as ArrayBuffer).byteLength).toBe(120 * 32000 + 1024);
    return Response.json({ text: "完整录音" });
  });
  vi.stubGlobal("fetch", fetcher);
  const response = await proxyDictationRequest(new Request("http://web.test/dictation", {
    method: "POST", body: new Uint8Array(120 * 32000 + 1024),
  }), config, "transcribe");
  expect(response.status).toBe(200); expect(fetcher).toHaveBeenCalledOnce();
});
