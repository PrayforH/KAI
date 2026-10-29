"use client";
import Link from "next/link";
import type { ProjectTab } from "../lib/project-space";
import { useProjectSpace } from "./use-project-space";
import { ProjectSettings } from "./project-settings";
import {
  ProjectFilesDialog,
  ProjectKnowledgeDialog,
  ProjectSkillsDialog,
  ProjectModal,
} from "./project-resource-dialogs";
import {
  ProjectContextRail,
  ProjectConnectorsDialog,
} from "./project-context-rail";
import { ProjectComposer } from "./project-composer";
import { ProjectOverview } from "./project-overview";
import { ProjectTasks } from "./project-tasks";
import { ProjectResources, ProjectFacts } from "./project-resources";
import { ProjectResults } from "./project-results";
import styles from "./project-home.module.css";

/** The shared shell owns the existing sidebar; project work stays on one surface. */
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
  const closePanel = () => chooseTab("overview");
  return (
    <div className={styles.page}>
      <div className={styles.workspace}>
        <main className={styles.projectMain}>
          <div className={styles.projectScroll} aria-label="项目任务列表">
          <header className={styles.header}>
            <span className={styles.projectMark} aria-hidden="true">
              <svg viewBox="0 0 24 24">
                <path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10H3Z" />
              </svg>
            </span>
            <div>
              <h1>{project.name}</h1>
              <p className={styles.subtitle}>更新于 {new Intl.DateTimeFormat("zh-CN", { month: "long", day: "numeric" }).format(new Date(project.updatedAt))}</p>
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
          <ProjectOverview space={space} />
          </div>
          <ProjectComposer space={space} />
        </main>
        <ProjectContextRail space={space} />
      </div>
      {settingsOpen && (
        <ProjectModal title="项目设置" onClose={() => setSettingsOpen(false)}>
          <div className={styles.dialogContent}>
            <ProjectSettings space={space} />
          </div>
        </ProjectModal>
      )}
      {tab !== "overview" && (
        <ProjectModal
          title={
            tab === "tasks"
              ? "全部项目任务"
              : tab === "results"
                ? "项目成果"
                : "项目资料"
          }
          className={styles.drawer}
          onClose={closePanel}
        >
          <div className={styles.dialogContent}>
            {error && (
              <p className={styles.error} role="alert">
                {error}
              </p>
            )}
            {tab === "tasks" && <ProjectTasks space={space} />}
            {tab === "resources" && <ProjectResources space={space} />}
            {tab === "results" && <ProjectResults space={space} />}
          </div>
        </ProjectModal>
      )}
      {dialog === "instructions" && (
        <ProjectModal
          title="项目指令"
          subtitle="适用于本项目的所有任务，不会修改智能体自身的配置。"
          onClose={() => setDialog(null)}
        >
          <div className={styles.dialogContent}>
            <form onSubmit={(event) => void space.saveSettings(event, true)}>
              <label className={styles.field}>
                指令
                <textarea
                  className={styles.textarea}
                  aria-label="项目指令"
                  placeholder="告诉助手这个项目的背景、要求和交付格式…"
                  value={space.instructions}
                  maxLength={20000}
                  disabled={space.saving}
                  onChange={(event) =>
                    space.setInstructions(event.target.value)
                  }
                />
              </label>
              {error && (
                <p className={styles.error} role="alert">
                  {error}
                </p>
              )}
              <div className={styles.actions}>
                <button className={styles.primary} disabled={space.saving}>
                  {space.saving ? "保存中…" : "保存指令"}
                </button>
              </div>
            </form>
            <details className={styles.factsDisclosure}>
              <summary>项目事实与决定（{resources.memories.length}）</summary>
              <ProjectFacts space={space} />
            </details>
          </div>
        </ProjectModal>
      )}
      {dialog === "connectors" && <ProjectConnectorsDialog space={space} />}
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
