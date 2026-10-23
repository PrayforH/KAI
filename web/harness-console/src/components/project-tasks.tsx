"use client";
import type { ProjectSpaceState } from "./use-project-space";
import { ProjectTaskRows, projectEmpty } from "./project-space-shared";
import styles from "./project-home.module.css";

export function ProjectTasks({ space }: { space: ProjectSpaceState }) {
  const {
    tasks,
    taskError,
    query,
    setQuery,
    status,
    setStatus,
    agentFilter,
    setAgentFilter,
    includeArchived,
    setIncludeArchived,
    loadingMore,
    chooseTab,
    loadMore,
    visibleTasks,
    hasMore,
    agentLabels,
    taskAgentNames,
  } = space;
  return (
    <section className={styles.section}>
      <div className={styles.sectionHead}>
        <h2>项目任务</h2>
        <button
          className={styles.primary}
          onClick={() => chooseTab("overview")}
        >
          选择智能体并新建任务
        </button>
      </div>
      <div className={styles.toolbar}>
        <input
          className={styles.input}
          aria-label="搜索项目任务"
          placeholder={hasMore ? "搜索已加载任务…" : "搜索任务…"}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <select
          className={styles.select}
          aria-label="任务状态"
          value={status}
          onChange={(event) => setStatus(event.target.value)}
        >
          <option value="all">全部状态</option>
          <option value="active">执行中</option>
          <option value="attention">需要处理</option>
          <option value="succeeded">已完成</option>
          <option value="idle">待开始</option>
        </select>
        <select
          className={styles.select}
          aria-label="执行智能体"
          value={agentFilter}
          onChange={(event) => setAgentFilter(event.target.value)}
        >
          <option value="all">全部智能体</option>
          {[...taskAgentNames].map(([id, label]) => (
            <option key={id} value={id}>
              {label}
            </option>
          ))}
        </select>
        <label className={styles.check}>
          <input
            type="checkbox"
            checked={includeArchived}
            onChange={(event) => setIncludeArchived(event.target.checked)}
          />
          包含归档
        </label>
      </div>
      {taskError && (
        <p className={styles.error} role="alert">
          {taskError}
        </p>
      )}
      <p className={styles.count}>
        已加载{" "}
        {tasks.filter((task) => includeArchived || !task.archived_at).length} 项
        · 当前显示 {visibleTasks.length} 项{hasMore ? " · 可继续加载" : ""}
      </p>
      {visibleTasks.length ? (
        <ProjectTaskRows tasks={visibleTasks} agentLabels={agentLabels} />
      ) : (
        projectEmpty(
          "没有匹配的任务",
          "调整筛选，或在项目概览中选择智能体开始工作。",
        )
      )}
      {hasMore && (
        <div className={styles.more}>
          <button
            className={styles.button}
            disabled={loadingMore}
            onClick={() => void loadMore()}
          >
            {loadingMore ? "加载中…" : "加载更多任务"}
          </button>
        </div>
      )}
    </section>
  );
}
