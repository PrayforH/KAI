"use client";

import { useEffect, useState } from "react";
import { AttachmentPrimitive, useAttachment } from "@assistant-ui/react";
import { useUploadFeedback, uploadKey, type UploadFeedback } from "../lib/upload-feedback-store";
import { inputArtifactIdFromAttachment } from "../lib/input-attachment-adapter";
import {
  inputArtifactPreviewTarget,
  isInputArtifactId,
  requestArtifactPreview,
} from "../lib/input-artifact-preview";

export function FileUploadStatus({ item }: { item: UploadFeedback }) {
  // A thumbnail card is 80px wide, so the overlay shows the state at a glance and
  // the full sentence stays available to assistive tech and the tooltip.
  const label = item.status === "error" ? `上传失败：${item.message ?? "请重新添加"}`
    : item.status === "ready" ? "已就绪"
    : item.progress === 100 ? "正在处理…"
    : `正在上传${item.progress === undefined ? "…" : ` ${item.progress}%`}`;
  const short = item.status === "error" ? "上传失败"
    : item.status === "ready" ? "已就绪"
    : item.progress === undefined ? "上传中…"
    : item.progress === 100 ? "处理中…"
    : `${item.progress}%`;
  return <div className={`attachment-upload-status ${item.status}`} role="status" title={label}>
    <span className="upload-status-short" aria-hidden="true">{short}</span>
    <span className="upload-status-text">{label}</span>
    {item.status === "uploading" && <div className="attachment-upload-track" role="progressbar"
      aria-label={`${item.fileName} 上传进度`} aria-valuemin={0} aria-valuemax={100}
      aria-valuenow={item.progress}>
      <span className={item.progress === undefined ? "indeterminate" : ""}
        style={item.progress === undefined ? undefined : { width: `${item.progress}%` }} />
    </div>}
  </div>;
}

function AttachmentFileIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true" focusable="false">
      <path d="M5.5 2.8h5.8l3.2 3.3v11.1H5.5Z" />
      <path d="M11.2 2.8v3.5h3.3" />
      <path d="M7.8 10h4.4M7.8 13h4.4" />
    </svg>
  );
}

function useAttachmentThumbSrc(file: File | undefined, fallback: string | undefined) {
  const [objectUrl, setObjectUrl] = useState<string>();
  useEffect(() => {
    if (!file) { setObjectUrl(undefined); return; }
    const next = URL.createObjectURL(file);
    setObjectUrl(next);
    return () => URL.revokeObjectURL(next);
  }, [file]);
  return objectUrl ?? fallback;
}

/**
 * Square image previews and named document cards. Transfer state rides on each card, so
 * the composer keeps no separate upload row above the input, and a finished
 * upload is silent apart from the ready mark.
 *
 * The whole card is one preview button: clicking it opens the same centered
 * dialog the transcript uses. Only the remove control sits above it.
 */
export function HarnessComposerAttachment() {
  const attachment = useAttachment((state) => state);
  const feedback = useUploadFeedback();
  const key = "file" in attachment && attachment.file ? uploadKey(attachment.file) : null;
  const item = feedback.find((entry) => entry.key === key);
  const state = item?.status ?? "ready";
  const resolvedArtifactId = inputArtifactIdFromAttachment(attachment);
  const artifactId = isInputArtifactId(resolvedArtifactId) ? resolvedArtifactId : null;
  const mediaType = attachment.contentType
    ?? ("file" in attachment ? attachment.file?.type : undefined)
    ?? "";
  const imageFallback = attachment.content
    ?.flatMap((part) => (part.type === "image" ? [part.image] : []))[0];
  // 只有图片才有可画的缩略图：给 PDF/文档一个 blob: 地址只会渲染成破图。
  const thumbSrc = useAttachmentThumbSrc(
    attachment.type === "image" && "file" in attachment ? attachment.file : undefined,
    attachment.type === "image" ? imageFallback : undefined,
  );
  const previewable = state !== "error" && artifactId !== null;
  const typeLabel = attachment.type === "image" ? "图片"
    : attachment.type === "document" ? "文档"
    : "文件";
  const card = <>
    <span className="aui-attachment-thumb">
      {thumbSrc
        // Object URLs and the same-origin artifact route both belong to this user.
        // eslint-disable-next-line @next/next/no-img-element
        ? <img src={thumbSrc} alt="" />
        : <AttachmentFileIcon />}
    </span>
    <div className="aui-attachment-text">
      <p className="aui-attachment-name">{attachment.name}</p>
      <p className="aui-attachment-type">{typeLabel}</p>
    </div>
  </>;
  return <div className="composer-file-card" data-kind={attachment.type === "image" ? "image" : "file"} data-upload-state={state}>
    <AttachmentPrimitive.Root className="aui-attachment-root">
      {previewable && artifactId !== null ? (
        <button
          type="button"
          className="aui-attachment-content"
          aria-label={`预览 ${attachment.name}`}
          title={`预览 ${attachment.name}`}
          onClick={() => requestArtifactPreview(inputArtifactPreviewTarget({
            artifactId,
            name: attachment.name,
            mediaType,
            sizeBytes: "file" in attachment ? attachment.file?.size ?? null : null,
          }))}
        >
          {card}
        </button>
      ) : <div className="aui-attachment-content">{card}</div>}
      <AttachmentPrimitive.Remove asChild>
        <button
          type="button"
          className="aui-attachment-remove"
          aria-label={`移除 ${attachment.name}`}
          title={`移除 ${attachment.name}`}
        >
          {/* 没有 fill/stroke 的折线路径不会画出任何东西，叉号必须描边。 */}
          <svg viewBox="0 0 16 16" aria-hidden="true" focusable="false">
            <path d="M4.4 4.4l7.2 7.2M11.6 4.4l-7.2 7.2" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
          </svg>
        </button>
      </AttachmentPrimitive.Remove>
    </AttachmentPrimitive.Root>
    {item && item.status !== "ready" ? <FileUploadStatus item={item} /> : null}
    {attachment.type !== "image" && <span className="composer-file-format" aria-hidden="true">{attachment.name.split(".").at(-1)?.toUpperCase() || "文件"}</span>}
  </div>;
}
