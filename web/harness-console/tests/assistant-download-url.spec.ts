import { describe, expect, it } from "vitest";

import { resolveDownloadUrl } from "../src/lib/assistant-download-url";

describe("resolveDownloadUrl", () => {
  it("rewrites the server's /v1 prefix to the web proxy's /api prefix", () => {
    expect(
      resolveDownloadUrl(
        "/v1/studio/assistant/groups/g1/library/documents/d1/content",
      ),
    ).toBe("/api/studio/assistant/groups/g1/library/documents/d1/content");
  });

  it("keeps paths that already use /api unchanged", () => {
    expect(resolveDownloadUrl("/api/studio/assistant/groups/g1/library")).toBe(
      "/api/studio/assistant/groups/g1/library",
    );
  });

  it("keeps external http(s) links unchanged", () => {
    expect(resolveDownloadUrl("https://cdn.example.com/a.pdf")).toBe(
      "https://cdn.example.com/a.pdf",
    );
    expect(resolveDownloadUrl("http://cdn.example.com/a.pdf")).toBe(
      "http://cdn.example.com/a.pdf",
    );
  });

  it("returns null for empty values so callers render no link", () => {
    expect(resolveDownloadUrl(undefined)).toBeNull();
    expect(resolveDownloadUrl(null)).toBeNull();
    expect(resolveDownloadUrl("")).toBeNull();
    expect(resolveDownloadUrl("   ")).toBeNull();
  });

  it("returns unknown shapes unchanged instead of throwing", () => {
    expect(resolveDownloadUrl("/download/d1")).toBe("/download/d1");
    expect(resolveDownloadUrl("/v1")).toBe("/v1");
  });
});
