"use client";
import Link from "next/link";
import type { ProjectSpaceState } from "./use-project-space";
import { ProjectTaskRows, projectEmpty, size } from "./project-space-shared";
import styles from "./project-home.module.css";
import { TaskAgentSwitcher } from "./task-agent-switcher";

export function ProjectOverview({ space }: { space: ProjectSpaceState }) {
  const {
    resources,
    artifacts,
    agents,
    selectedAgent,
    setSelectedAgent,
    taskError,
    resultError,
    creating,
    more,
    setDialog,
    chooseTab,
    startTask,
    project,
    activeTasks,
    pending,
    recent,
    agentLabels,
  } = space;
  if (!project) return null;
  if (!resources) return null;
  return (
    <div className={styles.grid}>
      <div>
        <div className={styles.stats}>
          <div className={styles.stat}>
            <strong>
              {activeTasks.length}
              {more.active ? "+" : ""}
            </strong>
            <span>项目任务</span>
          </div>
          <div className={styles.stat}>
            <strong>
              {pending.length}
              {more.active ? "+" : ""}
            </strong>
            <span>执行中与待处理</span>
          </div>
          <div className={styles.stat}>
            <strong>
              {artifacts.length}
              {artifacts.length === 500 ? "+" : ""}
            </strong>
            <span>已归集成果</span>
          </div>
        </div>
        <section className={styles.section}>
          <div className={styles.sectionHead}>
            <h2>继续推进</h2>
            <button
              className={styles.textLink}
              onClick={() => chooseTab("tasks")}
            >
              查看项目任务 →
            </button>
          </div>
          {taskError && <p className={styles.error}>{taskError}</p>}
          {recent.length ? (
            <ProjectTaskRows tasks={recent} agentLabels={agentLabels} />
          ) : (
            projectEmpty(
              "开始这个项目的第一项任务",
              "先添加资料与知识，再选择执行智能体。任务会在主对话中继续。",
            )
          )}
        </section>
        <section className={styles.section}>
          <div className={styles.sectionHead}>
            <h2>最近成果</h2>
            <button
              className={styles.textLink}
              onClick={() => chooseTab("results")}
            >
              查看成果 →
            </button>
          </div>
          {resultError && <p className={styles.error}>{resultError}</p>}
          {artifacts.length ? (
            <ul className={styles.list}>
              {artifacts.slice(0, 3).map((artifact) => (
                <li className={styles.row} key={artifact.artifact_id}>
                  <Link
                    className={styles.rowLink}
                    href={`/?thread=${encodeURIComponent(artifact.thread_id)}`}
                  >
                    <strong>{artifact.name}</strong>
                    <small>
                      {artifact.thread_title} · {size(artifact.size_bytes)}
                    </small>
                  </Link>
                  <button
                    className={styles.textLink}
                    onClick={() => chooseTab("results")}
                  >
                    查看
                  </button>
                </li>
              ))}
            </ul>
          ) : (
            projectEmpty(
              "成果会在这里汇总",
              "项目任务产生的文件按来源归集，归档任务的成果也会保留。",
            )
          )}
        </section>
      </div>
      <aside>
        <section className={`${styles.section} ${styles.side}`}>
          <h2>新建项目任务</h2>
          <p className={styles.muted}>
            选择已发布的智能体，带上本项目资料，在主对话中执行。
          </p>
          <TaskAgentSwitcher
            label="执行智能体"
            description="为项目新任务选择已发布的智能体"
            menuPlacement="below"
            agents={agents}
            selected={selectedAgent}
            loading={creating}
            currentTaskBusy={false}
            onChange={setSelectedAgent}
          />
          <button
            className={styles.primary}
            disabled={creating || !selectedAgent || Boolean(project.archivedAt)}
            onClick={() => void startTask()}
          >
            {creating
              ? "正在创建…"
              : project.archivedAt
                ? "项目已归档"
                : "新建任务 →"}
          </button>
          <p className={styles.muted}>
            能力配置与发布版本由
            <Link href="/studio/agents">智能体工作台</Link>管理。
          </p>
        </section>
        <section className={`${styles.section} ${styles.resources}`}>
          <h2>项目资源</h2>
          <button
            className={styles.resource}
            onClick={() => chooseTab("resources")}
          >
            <span>知识库</span>
            <span>{project.knowledgeReferences?.length ?? 0} 个 →</span>
          </button>
          <button
            className={styles.resource}
            onClick={() => chooseTab("resources")}
          >
            <span>文件与网页</span>
            <span>
              {resources.documents.length + resources.links.length} 项 →
            </span>
          </button>
          <button
            className={styles.resource}
            onClick={() => setDialog("skills")}
          >
            <span>附加技能</span>
            <span>{project.skillReferences?.length ?? 0} 项 →</span>
          </button>
        </section>
      </aside>
    </div>
  );
}
