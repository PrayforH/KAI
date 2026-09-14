import { afterEach, describe, expect, it, vi } from "vitest";

import { studioClient } from "../src/lib/studio-client";

function stubFetch(response: Response) {
  const mock = vi.fn(async () => response);
  vi.stubGlobal("fetch", mock);
  return mock;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("knowledge document delete", () => {
  it("treats a 204 No Content delete as success", async () => {
    // The delete route answers 204 with an empty body; parsing it as JSON used
    // to surface "Unexpected end of JSON input" after a successful delete.
    stubFetch(new Response(null, { status: 204 }));
    await expect(
      studioClient.deleteKnowledgeDocument("weknora-173-verify", "doc-1"),
    ).resolves.toBeUndefined();
  });

  it("still surfaces engine failures", async () => {
    stubFetch(
      new Response(
        JSON.stringify({ detail: { code: "engine_error", message: "Knowledge not found" } }),
        { status: 502, headers: { "Content-Type": "application/json" } },
      ),
    );
    await expect(
      studioClient.deleteKnowledgeDocument("weknora-173-verify", "doc-1"),
    ).rejects.toThrow("Knowledge not found");
  });

  it("keeps parsing JSON payloads", async () => {
    stubFetch(
      new Response(JSON.stringify([{ documentId: "doc-1", title: "起诉书.pdf" }]), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    await expect(
      studioClient.listKnowledgeDocuments("weknora-173-verify"),
    ).resolves.toEqual([{ documentId: "doc-1", title: "起诉书.pdf" }]);
  });
});
