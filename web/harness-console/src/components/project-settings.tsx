"use client";
import Link from "next/link";
import type { ProjectSpaceState } from "./use-project-space";
import styles from "./project-home.module.css";
export function ProjectSettings({ space }: { space: ProjectSpaceState }) {
  const {
    setSettingsOpen,
    name,
    setName,
    instructions,
    setInstructions,
    saving,
    saveSettings,
    archiveProject,
    restoreProject,
    project,
  } = space;
  if (!project) return null;
  return (
    <form
      className={styles.settings}
      onSubmit={(event) => void saveSettings(event)}
    >
      <h2>项目设置</h2>
      <label className={styles.field}>
        项目名称
        <input
          className={styles.input}
          value={name}
          maxLength={120}
          required
          onChange={(event) => setName(event.target.value)}
        />
      </label>
      <label className={styles.field}>
        项目指令
        <textarea
          className={styles.textarea}
          value={instructions}
          maxLength={20000}
          onChange={(event) => setInstructions(event.target.value)}
        />
      </label>
      <p className={styles.muted}>
        指令用于当前项目的业务背景与交付要求。智能体的通用指令、工具和版本在
        <Link href="/studio/agents">智能体工作台</Link>维护。
      </p>
      <div className={styles.actions}>
        {!project.archivedAt && (
          <button
            type="button"
            className={styles.textLink}
            disabled={saving}
            onClick={() => void archiveProject()}
          >
            归档项目
          </button>
        )}
        {project.archivedAt && (
          <button
            type="button"
            className={styles.button}
            disabled={saving}
            onClick={() => void restoreProject()}
          >
            恢复项目
          </button>
        )}
        <button
          type="button"
          className={styles.button}
          onClick={() => setSettingsOpen(false)}
        >
          取消
        </button>
        <button className={styles.primary} disabled={saving || !name.trim()}>
          {saving ? "保存中…" : "保存项目设置"}
        </button>
      </div>
    </form>
  );
}
