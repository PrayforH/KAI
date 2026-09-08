"use client";
import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import type { CompleteAttachment } from "@assistant-ui/react";
function AttachmentFileIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M5.5 2.8h5.8l3.2 3.3v11.1H5.5Z" />
      <path d="M11.2 2.8v3.5h3.3" />
      <path d="M7.8 10h4.4M7.8 13h4.4" />
    </svg>
  );
}

export function inputArtifactDownloadHref(
  data: string | undefined,
  attachmentId: string,
) {
  const artifactId = data?.startsWith("input_artifact_")
    ? data
    : attachmentId.startsWith("input_artifact_")
      ? attachmentId
      : undefined;
  return artifactId
    ? `/api/input-artifacts/${encodeURIComponent(artifactId)}/content`
    : undefined;
}

export function MessageAttachmentView({ attachment }: { attachment: Pick<CompleteAttachment, "id" | "name" | "type" | "contentType"> & Partial<Pick<CompleteAttachment, "content">> }) {
  const [previewOpen, setPreviewOpen] = useState(false);
  const filePart = attachment.content?.find((part) => part.type === "file");
  const imagePart = attachment.content?.find((part) => part.type === "image");
  const data = filePart?.type === "file"
    ? filePart.data
    : imagePart?.type === "image"
      ? imagePart.image
      : undefined;
  const href = inputArtifactDownloadHref(data, attachment.id);
  const extension = attachment.name.split(".").at(-1)?.toUpperCase() || "文件";
  const contentType = attachment.contentType
    ?? (filePart?.type === "file" ? filePart.mimeType : undefined);
  const isImage =
    attachment.type === "image"
    || contentType?.startsWith("image/")
    || ["AVIF", "GIF", "HEIC", "HEIF", "JPEG", "JPG", "PNG", "WEBP"].includes(
      extension,
    );
  const imageSrc = isImage
    ? href ?? (data?.startsWith("data:") || data?.startsWith("http") ? data : undefined)
    : undefined;
  const content = (
    <>
      {imageSrc ? (
        <span className="message-attachment-preview">
          {/* The same-origin artifact endpoint enforces the current user scope. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={imageSrc} alt={`${attachment.name} 缩略图`} />
        </span>
      ) : (
        <span className="message-attachment-icon"><AttachmentFileIcon /></span>
      )}
      <span className="message-attachment-copy">
        <strong>{attachment.name}</strong>
        <small>
          {extension} {isImage ? "图片" : "文件"}
          {href ? (isImage ? " · 点击查看" : " · 点击下载") : ""}
        </small>
      </span>
    </>
  );
  useEffect(() => {
    if (!previewOpen) return;
    const previousOverflow = document.body.style.overflow;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setPreviewOpen(false);
    };
    document.body.style.overflow = "hidden";
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", closeOnEscape);
    };
  }, [previewOpen]);

  const preview = previewOpen && imageSrc
    ? createPortal(
        <div
          className="image-lightbox"
          role="dialog"
          aria-modal="true"
          aria-label={`${attachment.name} 原图预览`}
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setPreviewOpen(false);
          }}
        >
          <header className="image-lightbox-toolbar">
            <span className="image-lightbox-title">
              <small>上传原图</small>
              <strong>{attachment.name}</strong>
            </span>
            <span className="image-lightbox-actions">
              {href ? (
                <a href={href} download={attachment.name}>
                  下载原图
                </a>
              ) : null}
              <button
                type="button"
                onClick={() => setPreviewOpen(false)}
                aria-label="关闭原图预览"
                autoFocus
              >
                ×
              </button>
            </span>
          </header>
          <div className="image-lightbox-stage">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={imageSrc} alt={attachment.name} />
          </div>
        </div>,
        document.body,
      )
    : null;
  return (
    <>
      <div
        className="message-attachment-card"
        data-kind={isImage ? "image" : "file"}
      >
        {isImage && imageSrc ? (
          <button
            className="message-attachment-open"
            type="button"
            onClick={() => setPreviewOpen(true)}
            title={`放大查看 ${attachment.name}`}
          >
            {content}
          </button>
        ) : href ? (
          <a
            href={href}
            download={attachment.name}
            title={`下载 ${attachment.name}`}
          >
            {content}
          </a>
        ) : (
          <span className="message-attachment-static">{content}</span>
        )}
      </div>
      {preview}
    </>
  );
}
