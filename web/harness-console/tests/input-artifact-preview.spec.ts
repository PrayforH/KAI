import { describe, expect, it } from "vitest";
import {
  inputArtifactContentUrl,
  inputArtifactDownloadUrl,
  inputArtifactPreviewTarget,
  isInputArtifactId,
} from "../src/lib/input-artifact-preview";

describe("input artifact preview target", () => {
  it("carries the upload route instead of the thread artifact route", () => {
    const target = inputArtifactPreviewTarget({
      artifactId: "input_artifact_abc",
      name: "季度报告.pdf",
      mediaType: "application/pdf",
      sizeBytes: 2048,
    });
    expect(target).toEqual({
      artifact_id: "input_artifact_abc",
      name: "季度报告.pdf",
      media_type: "application/pdf",
      size_bytes: 2048,
      content_url: "/api/input-artifacts/input_artifact_abc/content?inline=1",
      download_url: "/api/input-artifacts/input_artifact_abc/content",
    });
  });

  it("encodes ids and keeps preview inline while download stays an attachment", () => {
    expect(inputArtifactContentUrl("input_artifact/a b")).toBe(
      "/api/input-artifacts/input_artifact%2Fa%20b/content?inline=1",
    );
    expect(inputArtifactDownloadUrl("input_artifact/a b")).toBe(
      "/api/input-artifacts/input_artifact%2Fa%20b/content",
    );
  });

  it("only treats uploaded input artifacts as previewable", () => {
    expect(isInputArtifactId("input_artifact_abc")).toBe(true);
    expect(isInputArtifactId("artifact_abc")).toBe(false);
    expect(isInputArtifactId("upload:123")).toBe(false);
    expect(isInputArtifactId(undefined)).toBe(false);
  });

  it("defaults a missing media type to empty so the extension decides the kind", () => {
    const target = inputArtifactPreviewTarget({ artifactId: "input_artifact_a", name: "clip.mp4" });
    expect(target.media_type).toBe("");
    expect(target.size_bytes).toBeNull();
  });
});
