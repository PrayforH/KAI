"use client";
import { MessageAttachmentView } from "../message-attachment-view";
import { inputAttachmentType } from "../../lib/input-attachment-adapter";
import styles from "./build-workspace.module.css";
export type WorkspaceFile = {id: string; name: string; mediaType?: string};
export function WorkspaceAttachments({files, onRemove, disabled = false}: {files: WorkspaceFile[]; onRemove?: (id: string) => void; disabled?: boolean}) {
  return <div className={styles.fileChips} data-workspace-attachments>{files.map(file => <div className={styles.fileChip} key={file.id}>
    <MessageAttachmentView attachment={{id: file.id, name: file.name, type: inputAttachmentType(file.mediaType || "", file.name), contentType: file.mediaType, content: []}}/>
    {onRemove && <button className={styles.removeFile} type="button" aria-label={`移除 ${file.name}`} disabled={disabled} onClick={()=>onRemove(file.id)}>×</button>}
  </div>)}</div>;
}
