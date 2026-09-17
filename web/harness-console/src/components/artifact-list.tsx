export interface ArtifactDetails {
  artifact_id: string;
  run_id: string;
  thread_id?: string | null;
  name?: string;
  media_type?: string;
  size_bytes?: number;
  sha256?: string;
}

export function formatBytes(value?: number) {
  if (!value) return "0 B";
  if (value < 1024) return `${value} B`;
  return `${(value / 1024).toFixed(1)} KB`;
}

export function ArtifactCard({ details }: { details: ArtifactDetails }) {
  const contentUrl = `/api/harness/artifacts/${encodeURIComponent(details.artifact_id)}`;
  const mediaType = details.media_type || "application/octet-stream";
  const previewable =
    mediaType.startsWith("text/") ||
    mediaType.startsWith("image/") ||
    mediaType === "application/json" ||
    mediaType === "application/pdf";
  const primaryUrl = previewable ? `${contentUrl}?preview=1` : contentUrl;
  // Previewable artifacts open in the task rail, next to the conversation,
  // instead of a new tab that has to be closed again.
  const openInRail = (event: React.MouseEvent<HTMLAnchorElement>) => {
    if (!previewable || typeof window === "undefined") return;
    event.preventDefault();
    window.dispatchEvent(
      new CustomEvent("harness:preview-artifact", {
        detail: {
          artifact_id: details.artifact_id,
          name: details.name || "未命名产物",
          media_type: mediaType,
          size_bytes: details.size_bytes ?? null,
          thread_id: details.thread_id ?? null,
        },
      }),
    );
  };
  const filemark = mediaType.includes("json")
    ? "JSON"
    : mediaType.includes("pdf")
      ? "PDF"
      : mediaType.startsWith("image/")
        ? "IMG"
        : "FILE";
  return (
    <section className="domain-card artifact-domain-card">
      <a
        className="artifact-primary-link"
        href={primaryUrl}
        onClick={openInRail}
        rel={previewable ? "noreferrer" : undefined}
        download={previewable ? undefined : details.name}
        aria-label={previewable ? `预览 ${details.name || "运行产物"}` : `下载 ${details.name || "运行产物"}`}
        title={previewable ? `在侧栏预览 ${details.name || "运行产物"}` : `点击下载 ${details.name || "运行产物"}`}
      >
        <div className="artifact-filemark" aria-hidden="true">
          {filemark}
        </div>
        <div className="artifact-copy">
          <h3>
            {details.name || "未命名产物"}
            <span aria-hidden="true">{previewable ? "↗" : "↓"}</span>
          </h3>
          <p>
            <span>运行产物</span> · {mediaType} · {formatBytes(details.size_bytes)}
            {details.sha256 && <code title={`sha256 ${details.sha256}`}> · {details.sha256.slice(0, 8)}</code>}
          </p>
        </div>
      </a>
    </section>
  );
}
