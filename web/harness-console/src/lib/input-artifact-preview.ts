import {
  artifactContentUrl,
  artifactDownloadUrl,
  previewKindFor,
  type PreviewKind,
  type PreviewTarget,
} from "../components/rail-file-preview";

/** Uploaded files live on the input-artifact proxy route, not on a thread's
 * artifact route. Every entry point (composer card, sent bubble) builds the
 * same target here so the shared dialog previews and downloads the same URL. */
const INPUT_ARTIFACT_PREFIX = "input_artifact_";

export function isInputArtifactId(value: string | null | undefined): value is string {
  return typeof value === "string" && value.startsWith(INPUT_ARTIFACT_PREFIX);
}

export function inputArtifactContentUrl(inputArtifactId: string): string {
  // `inline=1` asks the route for inline disposition, so PDFs and text render
  // in the dialog instead of downloading.
  return `/api/input-artifacts/${encodeURIComponent(inputArtifactId)}/content?inline=1`;
}

export function inputArtifactDownloadUrl(inputArtifactId: string): string {
  return `/api/input-artifacts/${encodeURIComponent(inputArtifactId)}/content`;
}

export function inputArtifactPreviewTarget(input: {
  artifactId: string;
  name: string;
  mediaType?: string | null;
  sizeBytes?: number | null;
}): PreviewTarget {
  return {
    artifact_id: input.artifactId,
    name: input.name,
    media_type: input.mediaType ?? "",
    size_bytes: input.sizeBytes ?? null,
    content_url: inputArtifactContentUrl(input.artifactId),
    download_url: inputArtifactDownloadUrl(input.artifactId),
  };
}

/** Kinds a plain tab can render without running artifact-supplied script on
 * the console origin: the browser's own image/video/PDF viewers. */
const INLINE_FALLBACK_KINDS = new Set<PreviewKind>(["image", "video", "pdf"]);

/** HTML, SVG and unknown types can carry script, and the content route answers
 * inline on this origin, so a bare tab would run it with the user's session.
 * Only the browser-native viewers above may open inline; everything else is
 * downloaded instead. */
export function canOpenInlineWithoutHost(target: PreviewTarget): boolean {
  const mediaType = (target.media_type ?? "").toLowerCase();
  const extension = (target.name ?? "").toLowerCase().split(".").pop() ?? "";
  // SVG is image/svg+xml but is a scriptable document, not a raster image.
  if (mediaType.includes("svg") || extension === "svg") return false;
  return INLINE_FALLBACK_KINDS.has(previewKindFor(mediaType, target.name ?? ""));
}

/** One event name for every preview entry, handled by the assistant surface.
 * 没有预览宿主的页面（如智能体工作台）退回浏览器原生预览或下载，
 * 别让点击变成空操作，也别让附件在这份源上执行脚本。 */
export function requestArtifactPreview(target: PreviewTarget): void {
  if (typeof window === "undefined") return;
  const detail: PreviewTarget & { handled?: boolean } = { ...target, handled: false };
  window.dispatchEvent(new CustomEvent("harness:preview-artifact", { detail }));
  if (detail.handled) return;
  try {
    if (canOpenInlineWithoutHost(target)) {
      window.open(artifactContentUrl(target), "_blank", "noopener,noreferrer");
      return;
    }
    const anchor = document.createElement("a");
    anchor.href = artifactDownloadUrl(target);
    anchor.download = target.name;
    anchor.rel = "noopener";
    anchor.click();
  } catch {
    // 无头渲染等环境没有新窗口可开：没有预览宿主时只能到此为止。
  }
}
