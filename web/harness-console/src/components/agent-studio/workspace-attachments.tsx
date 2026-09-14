"use client";
import { FileUploadStatus } from "../composer-attachment";
import { useUploadFeedback, uploadFeedbackStore } from "../../lib/upload-feedback-store";
import { MessageAttachmentView } from "../message-attachment-view";
import { inputAttachmentType } from "../../lib/input-attachment-adapter";
import styles from "./build-workspace.module.css";
export type WorkspaceFile = {id: string; name: string; mediaType?: string; uploadKey?: string};
export function WorkspaceAttachments({files, onRemove, disabled = false}: {files: WorkspaceFile[]; onRemove?: (id: string) => void; disabled?: boolean}) {
  const feedback = useUploadFeedback();
  return <div className={styles.fileChips} data-workspace-attachments>{files.map(file => <div className={`${styles.fileChip} ${file.uploadKey ? "workspace-file-upload" : ""}`} key={file.id}>
    {file.uploadKey ? <div className="workspace-file-pending"><strong>{file.name}</strong><FileUploadStatus item={feedback.find(item => item.key === file.uploadKey) ?? {key: file.uploadKey, fileName: file.name, status: "uploading"}} /></div> : <MessageAttachmentView attachment={{id: file.id, name: file.name, type: inputAttachmentType(file.mediaType || "", file.name), contentType: file.mediaType, content: []}}/>}
    {onRemove && <button className={styles.removeFile} type="button" aria-label={`移除 ${file.name}`} disabled={disabled} onClick={()=>{ if(file.uploadKey) uploadFeedbackStore.dismiss(file.uploadKey); onRemove(file.id); }}>×</button>}
  </div>)}</div>;
}
