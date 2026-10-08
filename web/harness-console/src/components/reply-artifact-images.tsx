"use client";

import { useState } from "react";
import { groupWorkspaceFiles } from "../lib/workspace-file-groups";
import { artifactPreviewUrl, isImageArtifact, type ArtifactDetails } from "./artifact-list";

/** Only final images from this answer; extracted pages and duplicate events stay out. */
export function replyImageArtifacts(artifacts: ArtifactDetails[], answer: string) {
  const shown = new Set<string>();
  for (const match of answer.matchAll(/!\[[^\]]*\]\(\s*<?([^\s)>]+)/g)) {
    try { shown.add(new URL(match[1], "https://kai.invalid").pathname); } catch { /* Ignore invalid Markdown URLs. */ }
  }
  const groups = groupWorkspaceFiles(artifacts.map(file => ({ ...file, name: file.name ?? "未命名图片", media_type: file.media_type ?? "" })));
  const final = [...(groups.find(group => group.name === "最终产出")?.folders.values() ?? [])].flat();
  const finalIds = new Set(final.map(file => file.artifact_id));
  const ids = new Set<string>();
  return artifacts.filter(file => {
    const path = `/api/harness/artifacts/${encodeURIComponent(file.artifact_id)}`;
    if (!file.artifact_id || !finalIds.has(file.artifact_id) || !isImageArtifact(file) || ids.has(file.artifact_id) || shown.has(path)) return false;
    ids.add(file.artifact_id);
    return true;
  });
}

export function ReplyArtifactImages({ artifacts, answer, onPreview }: {
  artifacts: ArtifactDetails[];
  answer: string;
  onPreview?: (artifact: ArtifactDetails) => void;
}) {
  const [failed, setFailed] = useState<readonly string[]>([]);
  const images = replyImageArtifacts(artifacts, answer);
  if (images.length === 0) return null;
  return <div className="reply-artifact-images" aria-label="回复中的图片产出">
    {images.map(image => {
      const name = (image.name ?? "图片产出").split("/").at(-1) || "图片产出";
      const url = artifactPreviewUrl(image);
      return <figure className="reply-artifact-image" key={image.artifact_id}>
        {failed.includes(image.artifact_id) ? <a href={url} download={name}>下载 {name}</a> : <button
          type="button" className="reply-artifact-image-preview" aria-label={`查看大图 ${name}`}
          onClick={() => onPreview ? onPreview(image) : window.dispatchEvent(new CustomEvent("harness:preview-artifact", {
            detail: { ...image, expanded: true },
          }))}
        >
          {/* Same-origin authenticated content; intrinsic proportions are retained. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={url} alt={name} loading="lazy" decoding="async"
            onError={() => setFailed(current => [...current, image.artifact_id])} />
        </button>}
        <figcaption>{name}</figcaption>
      </figure>;
    })}
  </div>;
}
