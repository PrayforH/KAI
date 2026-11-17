"use client";
import { CodexResponseIcon } from "./codex-response-icon";

import { useState } from "react";
import { groupWorkspaceFiles } from "../lib/workspace-file-groups";
import { artifactPreviewUrl, type ArtifactDetails } from "./artifact-list";
import styles from "./video-generation-result.module.css";

/** The same published artifacts are replayed for live and historical answers. */
export function replyVideoArtifacts(artifacts: ArtifactDetails[]) {
  const groups = groupWorkspaceFiles(artifacts.map(file => ({
    ...file, name: file.name ?? "视频产出", media_type: file.media_type ?? "",
  })));
  const finalIds = new Set([...(groups.find(group => group.name === "最终产出")?.folders.values() ?? [])]
    .flat().map(file => file.artifact_id));
  const seen = new Set<string>();
  return artifacts.filter(file => {
    const video = (file.media_type ?? "").toLowerCase().startsWith("video/") ||
      /\.(mp4|webm|mov|m4v|ogv|avi|mkv)$/i.test(file.name ?? "");
    if (!file.artifact_id || !video || !finalIds.has(file.artifact_id) || seen.has(file.artifact_id)) return false;
    seen.add(file.artifact_id);
    return true;
  });
}

export function ReplyArtifactVideos({ artifacts }: { artifacts: ArtifactDetails[] }) {
  const [failed, setFailed] = useState<readonly string[]>([]);
  const videos = replyVideoArtifacts(artifacts);
  if (videos.length === 0) return null;
  return <div className="reply-artifact-videos" aria-label="回复中的视频产出">
    {videos.map(file => {
      const name = (file.name ?? "视频产出").replaceAll("\\", "/").split("/").at(-1) || "视频产出";
      const previewUrl = artifactPreviewUrl(file);
      const downloadUrl = new URL(previewUrl, "https://kai.invalid");
      downloadUrl.searchParams.delete("preview");
      // Same centered dialog as images and HTML: the inline player stays for
      // quick viewing, this opens the enlarged one.
      const openInDialog = () => window.dispatchEvent(new CustomEvent("harness:preview-artifact", {
        detail: {
          artifact_id: file.artifact_id,
          name,
          media_type: file.media_type || "video/mp4",
          size_bytes: file.size_bytes ?? null,
          thread_id: file.thread_id ?? null,
          expanded: true,
        },
      }));
      return <figure className={styles.response} key={file.artifact_id}>
        <video className={styles.player} src={previewUrl} controls playsInline preload="metadata"
          aria-label={`播放 ${name}`}
          onError={() => setFailed(current => current.includes(file.artifact_id) ? current : [...current, file.artifact_id])}
          onLoadedData={() => setFailed(current => current.filter(id => id !== file.artifact_id))} />
        <figcaption className={styles.footer}>
          <span className={styles.metadata} style={{ overflowWrap: "anywhere", minWidth: 0 }}>{name}</span>
          <div className={styles.actions}>
            <button type="button" className={styles.action} onClick={openInDialog}
              aria-label={`放大预览 ${name}`} title="在弹窗中放大预览">
              <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false" fill="none" stroke="currentColor"
                strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
                <path d="M6 2.6H2.6V6M10 2.6h3.4V6M6 13.4H2.6V10M10 13.4h3.4V10" />
              </svg>
            </button>
            <a className={styles.action} href={`${downloadUrl.pathname}${downloadUrl.search}`} download={name}
              aria-label={`下载 ${name}`} title={`下载 ${name}`}>
              <CodexResponseIcon name="download" />
            </a>
          </div>
        </figcaption>
        {failed.includes(file.artifact_id) ? <p className={styles.error} role="status">暂时无法播放，可下载视频后查看。</p> : null}
      </figure>;
    })}
  </div>;
}
