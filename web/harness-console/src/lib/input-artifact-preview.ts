import { artifactContentUrl, type PreviewTarget } from "../components/rail-file-preview";

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

/** One event name for every preview entry, handled by the assistant surface.
 * 没有预览宿主的页面（如智能体工作台）退回浏览器原生预览，别让点击变成空操作。 */
export function requestArtifactPreview(target: PreviewTarget): void {
  if (typeof window === "undefined") return;
  const detail: PreviewTarget & { handled?: boolean } = { ...target, handled: false };
  window.dispatchEvent(new CustomEvent("harness:preview-artifact", { detail }));
  if (detail.handled) return;
  try {
    window.open(artifactContentUrl(target), "_blank", "noopener,noreferrer");
  } catch {
    // 无头渲染等环境没有新窗口可开：没有预览宿主时只能到此为止。
  }
}
