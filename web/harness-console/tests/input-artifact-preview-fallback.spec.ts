// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { inputArtifactPreviewTarget, requestArtifactPreview } from "../src/lib/input-artifact-preview";

const target = inputArtifactPreviewTarget({
  artifactId: "input_artifact_a",
  name: "报告.pdf",
  mediaType: "application/pdf",
});

afterEach(() => vi.restoreAllMocks());

it("lets a mounted preview host take the click", () => {
  const listener = (event: Event) => { (event as CustomEvent).detail.handled = true; };
  window.addEventListener("harness:preview-artifact", listener);
  const open = vi.spyOn(window, "open").mockImplementation(() => null);
  try {
    requestArtifactPreview(target);
  } finally {
    window.removeEventListener("harness:preview-artifact", listener);
  }
  expect(open).not.toHaveBeenCalled();
});

it("falls back to the native preview on pages without a preview host", () => {
  const open = vi.spyOn(window, "open").mockImplementation(() => null);
  requestArtifactPreview(target);
  expect(open).toHaveBeenCalledWith(
    "/api/input-artifacts/input_artifact_a/content?inline=1",
    "_blank",
    "noopener,noreferrer",
  );
});
