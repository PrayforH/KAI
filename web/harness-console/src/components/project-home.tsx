"use client";
import Link from "next/link";
import type { ProjectTab } from "../lib/project-space";
import { useProjectSpace } from "./use-project-space";
import { TABS } from "./project-space-shared";
import { ProjectSettings } from "./project-settings";
import {
  ProjectFilesDialog,
  ProjectKnowledgeDialog,
  ProjectSkillsDialog,
} from "./project-resource-dialogs";
import styles from "./project-home.module.css";
import { ProjectOverview } from "./project-overview";
import { ProjectTasks } from "./project-tasks";
import { ProjectResources } from "./project-resources";
import { ProjectResults } from "./project-results";

/** Organize project work; execution stays in the shared conversation. */
export function ProjectHome({
  projectId,
  initialTab = "overview",
}: {
  projectId: string;
  initialTab?: ProjectTab;
}) {
  const space = useProjectSpace(projectId, initialTab);
  const {
    tab,
    resources,
    packages,
    loading,
    setLoading,
    error,
    notice,
    setNotice,
    dialog,
    setDialog,
    settingsOpen,
    setSettingsOpen,
    confirmationDialog,
    reload,
    refreshResources,
    chooseTab,
    project,
  } = space;
  if (loading)
    return (
      <div className={styles.page} aria-busy="true">
        <p className={styles.loading} role="status">
          正在读取项目…
        </p>
        <div className={styles.skeleton} />
        <div className={styles.skeleton} />
      </div>
    );
  if (!project || !resources)
    return (
      <div className={styles.page}>
        <h1>项目不可用</h1>
        <p role="alert">{error || "项目不存在，或你没有访问权限。"}</p>
        <button
          className={styles.button}
          onClick={() => {
            setLoading(true);
            void reload();
          }}
        >
          重试
        </button>{" "}
        <Link href="/">返回任务</Link>
      </div>
    );

  return (
    <div className={styles.page}>
      <p className={styles.eyebrow}>
        项目空间{project.archivedAt ? " · 已归档" : ""}
      </p>
      <header className={styles.header}>
        <div>
          <h1>{project.name}</h1>
          <p className={styles.subtitle}>
            {project.instructions?.trim()
              ? project.instructions.slice(0, 240)
              : "在这里整理业务资料、推进任务，并归集交付成果。"}
          </p>
        </div>
        <div className={styles.actions}>
          <button
            className={styles.button}
            aria-expanded={settingsOpen}
            onClick={() => setSettingsOpen((current) => !current)}
          >
            项目设置
          </button>
          <button
            className={styles.button}
            onClick={() => void reload()}
            aria-label="刷新项目"
          >
            刷新
          </button>
        </div>
      </header>
      {error && (
        <p className={styles.error} role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className={styles.notice} role="status">
          {notice}{" "}
          <button
            className={styles.textLink}
            aria-label="关闭提示"
            onClick={() => setNotice("")}
          >
            关闭
          </button>
        </p>
      )}
      {settingsOpen && <ProjectSettings space={space} />}
      <nav className={styles.tabs} role="tablist" aria-label="项目内容">
        {TABS.map(([id, label]) => (
          <button
            key={id}
            role="tab"
            id={`project-tab-${id}`}
            aria-selected={tab === id}
            aria-controls={`project-panel-${id}`}
            tabIndex={tab === id ? 0 : -1}
            className={styles.tab}
            onClick={() => chooseTab(id)}
            onKeyDown={(event) => {
              if (
                !["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)
              )
                return;
              event.preventDefault();
              const index = TABS.findIndex(([value]) => value === id);
              const nextIndex =
                event.key === "Home"
                  ? 0
                  : event.key === "End"
                    ? TABS.length - 1
                    : (index +
                        (event.key === "ArrowRight" ? 1 : -1) +
                        TABS.length) %
                      TABS.length;
              const next = TABS[nextIndex][0];
              chooseTab(next);
              document.getElementById(`project-tab-${next}`)?.focus();
            }}
          >
            {label}
          </button>
        ))}
      </nav>
      <section
        role="tabpanel"
        id={`project-panel-${tab}`}
        aria-labelledby={`project-tab-${tab}`}
      >
        {tab === "overview" && <ProjectOverview space={space} />}
        {tab === "tasks" && <ProjectTasks space={space} />}
        {tab === "resources" && <ProjectResources space={space} />}
        {tab === "results" && <ProjectResults space={space} />}
      </section>
      {dialog === "files" && (
        <ProjectFilesDialog
          project={project}
          mode="manage"
          onClose={() => setDialog(null)}
          onChanged={() => void refreshResources()}
        />
      )}
      {dialog === "knowledge" && (
        <ProjectKnowledgeDialog
          project={project}
          onClose={() => setDialog(null)}
          onChanged={() => void refreshResources()}
        />
      )}
      {dialog === "skills" && (
        <ProjectSkillsDialog
          project={project}
          packages={packages}
          onClose={() => setDialog(null)}
          onChanged={() => void refreshResources()}
        />
      )}
      {confirmationDialog}
    </div>
  );
}
