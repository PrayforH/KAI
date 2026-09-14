"use client";

import { AttachmentUI } from "@assistant-ui/react-ui";
import { useAttachment } from "@assistant-ui/react";
import { useUploadFeedback, uploadKey, type UploadFeedback } from "../lib/upload-feedback-store";

export function FileUploadStatus({ item }: { item: UploadFeedback }) {
  return <div className={`attachment-upload-status ${item.status}`} role="status" title={item.message}>
    <span>{item.status === "error" ? `上传失败：${item.message ?? "请重新添加"}`
      : item.status === "ready" ? "已就绪"
      : item.progress === 100 ? "正在处理…"
      : `正在上传${item.progress === undefined ? "…" : ` ${item.progress}%`}`}</span>
    {item.status === "uploading" && <div className="attachment-upload-track" role="progressbar"
      aria-label={`${item.fileName} 上传进度`} aria-valuemin={0} aria-valuemax={100}
      aria-valuenow={item.progress}>
      <span className={item.progress === undefined ? "indeterminate" : ""}
        style={item.progress === undefined ? undefined : { width: `${item.progress}%` }} />
    </div>}
  </div>;
}

export function HarnessComposerAttachment() {
  const attachment = useAttachment((state) => state);
  const feedback = useUploadFeedback();
  const key = "file" in attachment && attachment.file ? uploadKey(attachment.file) : null;
  const item = feedback.find((entry) => entry.key === key);
  return <div className="composer-file-card" data-upload-state={item?.status}>
    <AttachmentUI />
    {item && <FileUploadStatus item={item} />}
  </div>;
}
