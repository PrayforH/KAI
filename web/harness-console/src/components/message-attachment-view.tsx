"use client";
import { useState } from "react";
import { ConversationImageViewer } from "./conversation-image-viewer";
import type { CompleteAttachment } from "@assistant-ui/react";
import { inputArtifactIdFromAttachment } from "../lib/input-attachment-adapter";
import {
  inputArtifactPreviewTarget,
  isInputArtifactId,
  requestArtifactPreview,
} from "../lib/input-artifact-preview";

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

export type MessageAttachment = Pick<CompleteAttachment, "id" | "name" | "type" | "contentType"> & Partial<Pick<CompleteAttachment, "content">>;

export function attachmentDetails(attachment: MessageAttachment) {
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
  const resolvedId = inputArtifactIdFromAttachment(attachment);
  const artifactId = isInputArtifactId(resolvedId) ? resolvedId : undefined;
  return { href, extension, isImage, imageSrc, contentType, artifactId };
}

/** Sent files open in the same centered dialog as composer cards; anything the
 * console never uploaded keeps its download link or static chip. */
export function MessageAttachmentView({ attachment }: { attachment: MessageAttachment }) {
  const [fallbackOpen, setFallbackOpen] = useState(false);
  const { href, extension, isImage, imageSrc, contentType, artifactId } = attachmentDetails(attachment);
  const openPreview = () => {
    if (!artifactId) { setFallbackOpen(true); return; }
    requestArtifactPreview(inputArtifactPreviewTarget({
      artifactId,
      name: attachment.name,
      mediaType: contentType ?? "",
    }));
  };
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
        </small>
      </span>
    </>
  );
  return (
    <>
      <div
        className="message-attachment-card"
        data-kind={isImage ? "image" : "file"}
      >
        {(isImage && imageSrc) || artifactId ? (
          <button
            className="message-attachment-open"
            type="button"
            onClick={openPreview}
            title={`预览 ${attachment.name}`}
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
      {fallbackOpen && imageSrc && <ConversationImageViewer images={[{src: imageSrc, title: attachment.name, downloadHref: href}]} index={0} onSelect={() => {}} onClose={() => setFallbackOpen(false)} />}
    </>
  );
}


/** Compact sent attachments, with a bounded first view. */
export function MessageAttachmentGroup({ attachments }: { attachments: readonly MessageAttachment[] }) {
  const [expanded, setExpanded] = useState(false);
  if (!attachments.length) return null;
  const visible = expanded ? attachments : attachments.slice(0, 6);
  return <div className="aui-user-message-attachments message-attachment-group" aria-label={`附件 · ${attachments.length} 个`}>
    {visible.map(attachment => <MessageAttachmentView key={attachment.id} attachment={attachment} />)}
    {attachments.length > 6 && <button type="button" className="message-attachment-expand" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>{expanded ? "收起附件" : `+${attachments.length - 6} 个附件`}</button>}
  </div>;
}
