"use client";
import type { ProjectSpaceState } from "./use-project-space";
import { ProjectTaskRows, projectEmpty } from "./project-space-shared";
import { TaskAgentSwitcher } from "./task-agent-switcher";
import styles from "./project-home.module.css";

/** Project landing surface; file actions and execution stay in the conversation. */
export function ProjectOverview({ space }: { space: ProjectSpaceState }) {
  const {
    project,
    recent,
    taskError,
    agentLabels,
    chooseTab,
    draft,
    setDraft,
    creating,
    selectedAgent,
    agents,
    setSelectedAgent,
    startTask,
  } = space;
  if (!project) return null;
  return (
    <>
      <section
        className={styles.recentTasks}
        aria-labelledby="recent-project-tasks"
      >
        <div className={styles.sectionHead}>
          <h2 id="recent-project-tasks">最近</h2>
          <button
            className={styles.textLink}
            onClick={() => chooseTab("tasks")}
          >
            查看全部任务 ›
          </button>
        </div>
        {taskError && (
          <p role="alert" className={styles.error}>
            {taskError}
          </p>
        )}
        {recent.length ? (
          <ProjectTaskRows tasks={recent} agentLabels={agentLabels} />
        ) : (
          projectEmpty(
            "新建一个任务以开始",
            "在下方输入需求，任务将带上本项目的资料与指令。",
          )
        )}
      </section>
      <form
        className={styles.composer}
        aria-label="在项目中开始任务"
        onSubmit={(event) => {
          event.preventDefault();
          if (draft.trim()) void startTask(draft);
        }}
      >
        <textarea
          aria-label="项目任务需求"
          placeholder={
            project.archivedAt
              ? "项目已归档，恢复后可继续任务"
              : "在此项目中启动任务…"
          }
          value={draft}
          maxLength={100000}
          disabled={creating || Boolean(project.archivedAt)}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (
              event.key === "Enter" &&
              !event.shiftKey &&
              !event.nativeEvent.isComposing
            ) {
              event.preventDefault();
              event.currentTarget.form?.requestSubmit();
            }
          }}
        />
        <div className={styles.composerToolbar}>
          <TaskAgentSwitcher
            label="执行智能体"
            description="选择本次任务使用的智能体"
            menuPlacement="above"
            agents={agents}
            selected={selectedAgent}
            loading={creating}
            currentTaskBusy={false}
            onChange={setSelectedAgent}
          />
          <button
            className={styles.send}
            type="submit"
            aria-label="发送并开始项目任务"
            disabled={
              creating ||
              !selectedAgent ||
              !draft.trim() ||
              Boolean(project.archivedAt)
            }
          >
            {creating ? "…" : "↑"}
          </button>
        </div>
        <p className={styles.composerHint}>
          使用本项目的资料与指令 · Enter 发送，Shift + Enter 换行
        </p>
      </form>
    </>
  );
}
