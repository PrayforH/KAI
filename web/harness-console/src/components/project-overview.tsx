"use client";
import type { ProjectSpaceState } from "./use-project-space";
import { ProjectTaskRows, projectEmpty } from "./project-space-shared";
import styles from "./project-home.module.css";

/** Project landing surface; file actions and execution stay in the conversation. */
export function ProjectOverview({ space }: { space: ProjectSpaceState }) {
  const {
    project,
    recent,
    taskError,
    agentLabels,
    chooseTab,
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
          <ProjectTaskRows tasks={recent} agentLabels={agentLabels} onChanged={() => void space.reload()} compact />
        ) : (
          projectEmpty(
            "新建一个任务以开始",
            "在下方输入需求，任务将带上本项目的资料与指令。",
          )
        )}
      </section>
    </>
  );
}
