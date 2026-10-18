"use client";

import { useState } from "react";
import { projectClient, type ApiProject } from "../lib/studio-client";
import { ProjectModal } from "./project-resource-dialogs";
import styles from "./project-settings-dialog.module.css";

type Notice = { tone: "error" | "info"; text: string } | null;

export function ProjectSettingsDialog({
  project,
  onClose,
  onChanged,
  onRemoved,
}: {
  project: ApiProject;
  onClose: () => void;
  onChanged: (project: ApiProject) => void;
  onRemoved: (projectId: string) => void;
}) {
  const [name, setName] = useState(project.name);
  const [busy, setBusy] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [notice, setNotice] = useState<Notice>(null);

  async function save() {
    if (!name.trim() || busy) return;
    setBusy(true);
    setNotice(null);
    try {
      const updated = await projectClient.rename(project.projectId, name.trim());
      onChanged(updated);
      onClose();
    } catch (cause) {
      setNotice({
        tone: "error",
        text: cause instanceof Error ? cause.message : "保存失败，请重试。",
      });
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    if (busy) return;
    setBusy(true);
    setNotice(null);
    try {
      await projectClient.remove(project.projectId);
      onRemoved(project.projectId);
      onClose();
    } catch (cause) {
      setNotice({
        tone: "error",
        text: cause instanceof Error ? cause.message : "移除失败，请重试。",
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <ProjectModal
      title="项目设置"
      subtitle="重命名或移除这个项目。资源与任务记录会随项目一起移除。"
      onClose={onClose}
      footer={
        <>
          <button
            type="button"
            className={styles.danger}
            disabled={busy}
            onClick={() => setConfirmRemove(true)}
          >
            移除项目
          </button>
          <div className={styles.actions}>
            <button type="button" className={styles.secondary} onClick={onClose}>
              取消
            </button>
            <button
              type="button"
              className={styles.primary}
              disabled={busy || !name.trim()}
              onClick={() => void save()}
            >
              {busy ? "保存中…" : "保存"}
            </button>
          </div>
        </>
      }
    >
      {notice?.tone === "error" ? <p className={styles.error}>{notice.text}</p> : null}
      <label className={styles.field}>
        <span>项目名称</span>
        <input
          value={name}
          maxLength={120}
          onChange={(event) => setName(event.target.value)}
          autoFocus
        />
      </label>
      {confirmRemove ? (
        <div className={styles.confirm} role="alert">
          <p>确定移除「{project.name}」？项目文件和任务会一并清空，此操作不可撤销。</p>
          <div className={styles.actions}>
            <button
              type="button"
              className={styles.danger}
              disabled={busy}
              onClick={() => void remove()}
            >
              {busy ? "移除中…" : "确认移除"}
            </button>
            <button
              type="button"
              className={styles.secondary}
              onClick={() => setConfirmRemove(false)}
            >
              取消
            </button>
          </div>
        </div>
      ) : null}
    </ProjectModal>
  );
}
