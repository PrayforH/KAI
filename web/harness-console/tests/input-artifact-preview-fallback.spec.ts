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

it("opens raster images, video and PDF in a bare tab", () => {
  const open = vi.spyOn(window, "open").mockImplementation(() => null);
  for (const [name, mediaType] of [
    ["截图.png", "image/png"],
    ["录像.mp4", "video/mp4"],
    ["报告.pdf", "application/pdf"],
  ]) {
    open.mockClear();
    requestArtifactPreview(inputArtifactPreviewTarget({ artifactId: "input_artifact_a", name, mediaType }));
    expect(open).toHaveBeenCalledWith(
      "/api/input-artifacts/input_artifact_a/content?inline=1",
      "_blank",
      "noopener,noreferrer",
    );
  }
});

it("downloads HTML, SVG and unknown types instead of opening them on this origin", () => {
  const open = vi.spyOn(window, "open").mockImplementation(() => null);
  const clicks: HTMLAnchorElement[] = [];
  vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(function (this: HTMLAnchorElement) {
    clicks.push(this);
  });
  for (const [name, mediaType] of [
    ["页面.html", "text/html"],
    ["图标.svg", "image/svg+xml"],
    ["附件.bin", "application/octet-stream"],
  ]) {
    clicks.length = 0;
    requestArtifactPreview(inputArtifactPreviewTarget({ artifactId: "input_artifact_a", name, mediaType }));
    expect(open).not.toHaveBeenCalled();
    expect(clicks).toHaveLength(1);
    expect(clicks[0].getAttribute("href")).toBe("/api/input-artifacts/input_artifact_a/content");
    expect(clicks[0].getAttribute("download")).toBe(name);
  }
});
