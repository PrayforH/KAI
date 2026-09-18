"use client";

import { AttachmentUI } from "@assistant-ui/react-ui";
import { useAttachment } from "@assistant-ui/react";
import { useUploadFeedback, uploadKey, type UploadFeedback } from "../lib/upload-feedback-store";

export function FileUploadStatus({ item }: { item: UploadFeedback }) {
  // A thumbnail card is 72px wide, so the overlay shows the state at a glance and
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

/**
 * One square thumbnail per attachment. The transfer state rides on the card, so
 * the composer keeps no separate upload row above the input, and a finished
 * upload is silent apart from the ready mark.
 */
export function HarnessComposerAttachment() {
  const attachment = useAttachment((state) => state);
  const feedback = useUploadFeedback();
  const key = "file" in attachment && attachment.file ? uploadKey(attachment.file) : null;
  const item = feedback.find((entry) => entry.key === key);
  const state = item?.status ?? "ready";
  return <div className="composer-file-card" data-upload-state={state}>
    <AttachmentUI />
    {item && item.status !== "ready" ? <FileUploadStatus item={item} /> : null}
    {item?.status === "ready" ? <span className="composer-file-ready" aria-hidden="true">✓</span> : null}
  </div>;
}
