"use client";
import { useState } from "react";
import { ConversationImageViewer, type ConversationImage } from "./conversation-image-viewer";
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
  return { href, extension, isImage, imageSrc };
}

export function MessageAttachmentView({ attachment, onPreview }: { attachment: MessageAttachment; onPreview?: () => void }) {
  const [previewOpen, setPreviewOpen] = useState(false);
  const { href, extension, isImage, imageSrc } = attachmentDetails(attachment);
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
        {isImage && imageSrc ? (
          <button
            className="message-attachment-open"
            type="button"
            onClick={() => onPreview ? onPreview() : setPreviewOpen(true)}
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
      {previewOpen && imageSrc && <ConversationImageViewer images={[{src: imageSrc, title: attachment.name, downloadHref: href}]} index={0} onSelect={() => {}} onClose={() => setPreviewOpen(false)} />}
    </>
  );
}


/** Compact sent attachments, with a bounded first view and shared image navigation. */
export function MessageAttachmentGroup({ attachments }: { attachments: readonly MessageAttachment[] }) {
  const [expanded, setExpanded] = useState(false);
  const [index, setIndex] = useState<number | null>(null);
  const images: ConversationImage[] = attachments.flatMap(attachment => {
    const { imageSrc, href } = attachmentDetails(attachment);
    return imageSrc ? [{ src: imageSrc, title: attachment.name, downloadHref: href }] : [];
  });
  if (!attachments.length) return null;
  const visible = expanded ? attachments : attachments.slice(0, 6);
  return <div className="aui-user-message-attachments message-attachment-group" aria-label={`附件 · ${attachments.length} 个`}>
    {visible.map(attachment => {
      const imageIndex = images.findIndex(image => image.src === attachmentDetails(attachment).imageSrc);
      return <MessageAttachmentView key={attachment.id} attachment={attachment} onPreview={imageIndex >= 0 ? () => setIndex(imageIndex) : undefined} />;
    })}
    {attachments.length > 6 && <button type="button" className="message-attachment-expand" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}>{expanded ? "收起附件" : `+${attachments.length - 6} 个附件`}</button>}
    {index !== null && <ConversationImageViewer images={images} index={index} onSelect={setIndex} onClose={() => setIndex(null)} />}
  </div>;
}
