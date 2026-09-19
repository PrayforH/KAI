"use client";

import Link from "next/link";
import { PanelResizeHandle } from "./panel-resize-handle";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { AccountMenu } from "./account-menu";
import { useAuth } from "./auth-provider";
import { ProductBrandMark, PRODUCT_NAME } from "./product-brand";
import { WorkspaceNavigation, type WorkspaceId } from "./workspace-navigation";
import { useRunViewModel } from "../lib/activity-store";
import { approvalStore } from "../lib/approval-store";
import { useDialogFocus } from "../lib/use-dialog-focus";
import {
  loadTasks,
  markTaskRead,
  isTaskRead,
  peekCachedTasks,
  notifyTaskListChanged,
  prefetchThreadHistory,
  setTaskArchived,
  type TaskSummary,
} from "../lib/task-history";
import { formatTaskAge } from "../lib/task-list-age";
import { agentDisplayName } from "../lib/agent-display-name";
import {
  ApiProject,
  projectClient,
  setTaskProject,
} from "../lib/studio-client";
import { taskListRefreshDelay } from "../lib/task-list-refresh";


const statusLabels: Record<string, string> = {
  idle: "新任务",
  queued: "排队中",
  running: "运行中",
  waiting_approval: "待审批",
  cancelling: "取消中",
  cancelled: "已取消",
  succeeded: "已完成",
  failed: "处理错误",
  rejected: "已拒绝",
  timed_out: "已超时",
};

function NewTaskIcon() {
  return (
    <svg className="task-new-icon" viewBox="0 0 20 20" aria-hidden="true">
      <rect x="3.5" y="5.5" width="11" height="11" rx="2" />
      <path d="M8 13.2 8.5 11l6.8-6.8a1.4 1.4 0 0 1 2 2L10.5 13Z" />
    </svg>
  );
}

function AddToProjectIcon() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true">
      <path d="M8 3.5v9M3.5 8h9" />
    </svg>
  );
}

function SidebarCollapseIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <rect x="2.75" y="4.25" width="14.5" height="11.5" rx="2.5" />
      <path d="M6.75 4.25v11.5" />
    </svg>
  );
}

function ArchiveIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M3.5 6.5h13v9a1.5 1.5 0 0 1-1.5 1.5H5a1.5 1.5 0 0 1-1.5-1.5Z" />
      <path d="M2.5 3.5h15v3h-15Z" />
      <path d="M7.5 10h5" />
    </svg>
  );
}

function ProjectFolderIcon({ open = false }: { open?: boolean }) {
  return (
    <svg className="task-project-folder" viewBox="0 0 20 20" aria-hidden="true">
      <path d="M2.75 5.75a2 2 0 0 1 2-2h3.1l1.7 1.9h5.7a2 2 0 0 1 2 2v6.5a2 2 0 0 1-2 2H4.75a2 2 0 0 1-2-2Z" />
      {open && <path d="M5.5 9.75h9" />}
    </svg>
  );
}

function ScrollingTaskTitle({ title }: { title: string }) {
  const viewportRef = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    const viewport = viewportRef.current;
    const text = viewport?.firstElementChild as HTMLElement | null;
    const button = viewport?.closest("button");
    if (!viewport || !text || !button) return;
    const measure = () => {
      const overflow = Math.max(0, text.scrollWidth - viewport.clientWidth);
      viewport.style.setProperty("--task-title-overflow", `${-overflow}px`);
      // Travel the clipped part at roughly 40px per second plus a small fixed
      // lead-in, so hovering a long task name reveals it without a long wait.
      viewport.style.setProperty("--task-title-duration", `${Math.max(2.5, overflow / 40 + 1.4)}s`);
      viewport.dataset.overflow = String(overflow > 0);
    };
    measure();
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    observer?.observe(viewport);
    button.addEventListener("pointerenter", measure);
    button.addEventListener("focus", measure);
    return () => {
      observer?.disconnect();
      button.removeEventListener("pointerenter", measure);
      button.removeEventListener("focus", measure);
    };
  }, [title]);
  return <span ref={viewportRef} className="task-list-title task-title-viewport" title={title}>
    <span className="task-title-text">{title}</span>
  </span>;
}

const activeStatuses = new Set(["queued", "running", "waiting_approval", "cancelling"]);
const errorStatuses = new Set(["failed", "timed_out", "rejected"]);

export function TaskSidebar({
  currentThreadId,
  collapsed,
  overlayOpen = false,
  onToggle,
  onSelect,
  onNewTask,
  onNewTaskWithProject,
  searchControl,
  activeNav = "tasks",
  agentLabels,
  projects = [],
  onProjectsChanged,
  onCreateProject,
}: {
  currentThreadId: string;
  collapsed: boolean;
  overlayOpen?: boolean;
  onToggle: () => void;
  onSelect: (task: TaskSummary) => void;
  onNewTask: () => void;
  /** Start a task inside an explicit project, using any row of that group. */
  onNewTaskWithProject?: (projectTask: TaskSummary) => void;
  searchControl?: ReactNode;
  /** Which workspace nav item is highlighted; defaults to the task page. */
  activeNav?: WorkspaceId;
  /** Optional agent_name -> display label map; falls back to platform names. */
  agentLabels?: Readonly<Record<string, string>>;
  /** Projects shown above the plain task list, in creation order. */
  projects?: readonly ApiProject[];
  onProjectsChanged?: () => void;
  /** Opens the project creation flow owned by the page. */
  onCreateProject?: () => void;
}) {
  // Seed from the shared snapshot so navigating to a Studio page and back
  // (a fresh mount) renders the previous list immediately instead of
  // clearing it behind the loading state.
  const [tasks, setTasks] = useState<TaskSummary[]>(() => peekCachedTasks() ?? []);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(() => peekCachedTasks() === null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [updatingThreadId, setUpdatingThreadId] = useState("");
  const [showAllProjects, setShowAllProjects] = useState(false);
  const [collapsedProjects, setCollapsedProjects] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  const [expandedTaskGroups, setExpandedTaskGroups] = useState<ReadonlySet<string>>(() => new Set());
  const sidebarRef = useRef<HTMLElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const expandButtonRef = useRef<HTMLButtonElement>(null);
  const wasOverlayOpenRef = useRef(false);
  const runView = useRunViewModel();
  const { user } = useAuth();

  // Acknowledge only the version displayed on a visible task page. Server
  // watermarks are shared across devices; failed writes retry on list refresh.
  useEffect(() => {
    const current = tasks.find((task) => task.thread_id === currentThreadId);
    if (activeNav !== "tasks" || document.visibilityState === "hidden"
      || !current || activeStatuses.has(current.status) || isTaskRead(current)) return;
    let active = true;
    void markTaskRead(current.thread_id, current.updated_at).then((readAt) => {
      if (!active) return;
      setTasks((previous) => previous.map((task) => task.thread_id === current.thread_id
        ? { ...task, last_read_at: readAt } : task));
    }).catch(() => { /* Keep server truth; the next list refresh retries. */ });
    return () => { active = false; };
  }, [activeNav, currentThreadId, tasks, user.user_id]);

  useDialogFocus({
    open: overlayOpen,
    panelRef: sidebarRef,
    initialFocusRef: closeButtonRef,
    onEscape: onToggle,
  });

  useEffect(() => {
    if (overlayOpen) {
      wasOverlayOpenRef.current = true;
      return;
    }
    if (!wasOverlayOpenRef.current) return;
    wasOverlayOpenRef.current = false;
    const focusTimer = window.setTimeout(() => expandButtonRef.current?.focus(), 20);
    return () => window.clearTimeout(focusTimer);
  }, [overlayOpen]);

  useEffect(() => {
    approvalStore.reset(currentThreadId);
  }, [currentThreadId]);

  useEffect(() => {
    let active = true;
    let refreshing = false;
    let timer: number | undefined;

    function schedule(next: TaskSummary[], failed = false) {
      if (!active || document.visibilityState === "hidden") return;
      window.clearTimeout(timer);
      timer = window.setTimeout(
        () => void refresh(),
        taskListRefreshDelay(
          next.map((task) => task.status),
          runView?.phase,
          failed,
        ),
      );
    }

    async function refresh() {
      if (
        !active
        || refreshing
        || document.visibilityState === "hidden"
      ) return;
      refreshing = true;
      try {
        const next = await loadTasks(false);
        if (active) {
          setTasks(next);
          setError("");
          schedule(next);
        }
      } catch (cause) {
        if (active) {
          setError(cause instanceof Error ? cause.message : String(cause));
          schedule([], true);
        }
      } finally {
        refreshing = false;
        if (active) setLoading(false);
      }
    }

    function refreshWhenVisible() {
      if (document.visibilityState === "hidden") {
        window.clearTimeout(timer);
        return;
      }
      window.clearTimeout(timer);
      void refresh();
    }

    // Pin / rename / archive from the task header mutates the list outside
    // this component; refresh right away instead of waiting for the next tick.
    function refreshOnListChanged() {
      if (document.visibilityState === "hidden") return;
      window.clearTimeout(timer);
      void refresh();
    }

    refreshWhenVisible();
    document.addEventListener("visibilitychange", refreshWhenVisible);
    window.addEventListener("focus", refreshWhenVisible);
    window.addEventListener("harness:task-list-changed", refreshOnListChanged);
    return () => {
      active = false;
      window.clearTimeout(timer);
      document.removeEventListener("visibilitychange", refreshWhenVisible);
      window.removeEventListener("focus", refreshWhenVisible);
      window.removeEventListener("harness:task-list-changed", refreshOnListChanged);
    };
  }, [refreshKey, runView?.phase, user.user_id]);

  function retryTasks() {
    setError("");
    setLoading(true);
    setRefreshKey((current) => current + 1);
  }

  async function archiveTask(task: TaskSummary) {
    if (activeStatuses.has(task.status)) return;
    setUpdatingThreadId(task.thread_id);
    try {
      await setTaskArchived(task.thread_id, true);
      setTasks((current) => {
        const next = current.filter((item) => item.thread_id !== task.thread_id);
        return next;
      });
      setError("");
      notifyTaskListChanged();
      if (task.thread_id === currentThreadId) onNewTask();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setUpdatingThreadId("");
    }
  }

  const selected = useMemo(
    () => tasks.find((task) => task.thread_id === currentThreadId),
    [currentThreadId, tasks],
  );
  const labelForAgent = useMemo(
    () => (name: string) => agentLabels?.[name] ?? agentDisplayName(name),
    [agentLabels],
  );
  const byRecency = useMemo(
    () =>
      [...tasks].sort(
        (a, b) =>
          Number(Boolean(b.pinned_at)) - Number(Boolean(a.pinned_at)) ||
          Date.parse(b.updated_at) - Date.parse(a.updated_at),
      ),
    [tasks],
  );
  // 项目 first, then everything without a project. A project is a user-owned
  // container, so a task's Agent no longer decides where it appears.
  const projectGroups = useMemo(() => {
    const known = new Set(projects.map((project) => project.projectId));
    return projects.map((project) => ({
      project,
      tasks: byRecency.filter((task) => task.project_id === project.projectId),
    })).filter((group) => group.tasks.length > 0 || known.has(group.project.projectId));
  }, [projects, byRecency]);
  // A task whose project was deleted (or is not visible here) falls back to the
  // plain 任务 list instead of disappearing from both sections.
  const looseTasks = useMemo(() => {
    const known = new Set(projects.map((project) => project.projectId));
    return byRecency.filter(
      (task) => !task.project_id || !known.has(task.project_id),
    );
  }, [byRecency, projects]);

  useEffect(() => {
    if (!selected) return;
    if (selected.pending_approval) {
      approvalStore.show(selected.pending_approval, selected.thread_id);
    } else if (runView?.phase !== "waiting_approval") {
      approvalStore.clear(undefined, selected.thread_id);
    }
  }, [runView?.phase, selected]);

  function renderTaskRow(task: TaskSummary) {
    let status = task.status;
    if (runView && task.thread_id === currentThreadId && task.run_id === runView.runId) {
      if (runView.phase === "completed") status = "succeeded";
      else if (runView.phase === "failed") status = task.status === "timed_out" ? "timed_out" : "failed";
      else if (task.status !== "cancelling" || !activeStatuses.has(runView.phase)) status = runView.phase;
    }
    const hasError = errorStatuses.has(status);
    const statusLabel = statusLabels[status] ?? status;
    const unreadResult =
      status === "succeeded" &&
      task.thread_id !== currentThreadId &&
      !isTaskRead(task);
    const showStatusDot = activeStatuses.has(status) || hasError || unreadResult;
    return (
      <div
        role="listitem"
        key={task.thread_id}
        className="task-list-row"
      >
        <button
          type="button"
          className={`task-list-item ${task.thread_id === currentThreadId ? "is-active" : ""} ${task.pending_approval ? "needs-approval" : ""}`}
          aria-label={`${task.title}，${statusLabel}`}
          title={hasError ? `${statusLabel}，点击查看详情` : undefined}
          onPointerEnter={() => {
            void prefetchThreadHistory(task.thread_id).catch(() => {});
          }}
          onFocus={() => {
            void prefetchThreadHistory(task.thread_id).catch(() => {});
          }}
          onClick={() => onSelect(task)}
        >
          {showStatusDot && (
            <span
              className={`task-status ${unreadResult ? "status-unread" : `status-${status}`}`}
              aria-hidden="true"
            >
              {statusLabel}
            </span>
          )}
          <ScrollingTaskTitle title={task.title} />
          {hasError && <span className="task-error-label">{statusLabel}</span>}
          <span className="task-list-meta">
            <time dateTime={task.updated_at}>{formatTaskAge(task.updated_at)}</time>
          </span>
        </button>
        <button
          type="button"
          className="task-list-archive"
          onClick={() => void archiveTask(task)}
          disabled={
            updatingThreadId === task.thread_id
            || activeStatuses.has(task.status)
          }
          aria-label={`归档 ${task.title}`}
          title={
            activeStatuses.has(task.status)
              ? "任务结束后可归档"
              : "归档任务"
          }
        >
          <ArchiveIcon />
        </button>
      </div>
    );
  }

  return (
    <aside
      ref={sidebarRef}
      className={`task-sidebar ${collapsed ? "is-collapsed" : ""}`}
      aria-label={collapsed ? "任务快捷栏" : "任务列表"}
      aria-modal={overlayOpen ? true : undefined}
      data-task-sidebar-overlay={overlayOpen ? "true" : undefined}
      role={overlayOpen ? "dialog" : undefined}
    >
      {!collapsed && <PanelResizeHandle panel="sidebar" />}
      {collapsed ? (
        <div className="task-sidebar-rail" />
      ) : (
        <>
          <div className="task-sidebar-brand">
            <Link className="task-sidebar-brand-link" href="/" aria-label={`${PRODUCT_NAME}任务首页`}>
              <ProductBrandMark />
              <span className="task-sidebar-brand-copy task-workbench-name">
                <strong>{PRODUCT_NAME}</strong>
              </span>
            </Link>
            {searchControl ? (
              <div className="task-sidebar-search-control">{searchControl}</div>
            ) : null}
            <button
              ref={closeButtonRef}
              type="button"
              className="task-sidebar-collapse"
              onClick={onToggle}
              aria-label="收起任务列表"
              aria-expanded="true"
              title="收起任务列表"
            >
              <SidebarCollapseIcon />
            </button>
          </div>
          <div className="task-sidebar-primary">
            <button type="button" className="task-sidebar-create" onClick={onNewTask}>
              <NewTaskIcon />
              <span>新建任务</span>
            </button>
          </div>
          <div className="task-sidebar-mode">
            <WorkspaceNavigation
              active={activeNav}
              visible={["knowledge", "agents", "automation", "capabilities"]}
              labelOverrides={{ capabilities: "插件" }}
            />
          </div>
          <div className="task-list-toolbar">
            <div className="task-list-heading">
              <span className="task-list-heading-copy">
                <ProjectFolderIcon />
                项目
              </span>
            </div>
            <button
              type="button"
              className="task-project-create"
              aria-label="新建项目"
              title="新建项目"
              onClick={() => onCreateProject?.()}
            >
              ＋
            </button>
          </div>
          <div className="task-list" role="list">
            {projectGroups.map(({ project, tasks: projectTasks }) => {
              const collapsed = !expandedTaskGroups.has(`project:${project.projectId}`);
              const expanded = expandedTaskGroups.has(project.projectId);
              return (
                <section
                  className={`task-project-group task-user-project${collapsed ? " is-collapsed" : ""}`}
                  key={project.projectId}
                  aria-label={project.name}
                  data-project-id={project.projectId}
                >
                  <div className="task-project-head">
                    <button
                      type="button"
                      className="task-project-heading"
                      aria-expanded={!collapsed}
                      aria-label={`${collapsed ? "展开" : "收起"}项目 ${project.name}`}
                      title={`${collapsed ? "展开" : "收起"}项目 ${project.name}`}
                      onClick={() => {
                        setExpandedTaskGroups((current) => {
                          const next = new Set(current);
                          const key = `project:${project.projectId}`;
                          if (next.has(key)) next.delete(key);
                          else next.add(key);
                          return next;
                        });
                      }}
                    >
                      <ProjectFolderIcon open={!collapsed} />
                      <strong className="project-name-viewport">
                        <span
                          onMouseEnter={(event) => {
                            const node = event.currentTarget;
                            node.style.setProperty(
                              "--name-overflow",
                              `${Math.min(0, node.parentElement!.clientWidth - node.scrollWidth)}px`,
                            );
                          }}
                        >
                          {project.name}
                        </span>
                      </strong>
                      <span className="task-bucket-count">{projectTasks.length}</span>
                    </button>
                    {projectTasks.length > 0 && (
                      <button
                        type="button"
                        className="task-project-add"
                        aria-label={`在 ${project.name} 下新建任务`}
                        title={`在 ${project.name} 下新建任务`}
                        disabled={!onNewTaskWithProject}
                        onClick={() => {
                          if (onNewTaskWithProject && projectTasks[0]) {
                            onNewTaskWithProject(projectTasks[0]);
                          }
                        }}
                      >
                        <AddToProjectIcon />
                      </button>
                    )}
                  </div>
                  {!collapsed && (
                    <div className="task-project-items">
                      {projectTasks.length === 0 && (
                        <p className="task-project-empty">还没有任务，把任务移入这里即可</p>
                      )}
                      {(expanded ? projectTasks : projectTasks.slice(0, 5)).map(renderTaskRow)}
                      {projectTasks.length > 5 && (
                        <button
                          type="button"
                          className="task-list-item tasks-show-more"
                          aria-label={`${expanded ? "收起" : "展开"} ${project.name} 的任务`}
                          aria-expanded={expanded}
                          onClick={() =>
                            setExpandedTaskGroups((current) => {
                              const next = new Set(current);
                              if (next.has(project.projectId)) next.delete(project.projectId);
                              else next.add(project.projectId);
                              return next;
                            })
                          }
                        >
                          <span className="task-list-title">
                            {expanded ? "收起显示" : `展开显示（${projectTasks.length - 5}）`}
                          </span>
                        </button>
                      )}
                    </div>
                  )}
                </section>
              );
            })}
          </div>
          <div className="task-list-toolbar task-agent-section-heading">
            <div className="task-list-heading">
              <span className="task-list-heading-copy">任务</span>
            </div>
          </div>
          <div className="task-list" role="list">
            {looseTasks.length === 0 && tasks.length > 0 && (
              <p className="task-project-empty">所有任务都在项目中</p>
            )}
            <section className="task-project-group task-default-group" aria-label="任务">
              <div className="task-project-items">
                {(expandedTaskGroups.has("tasks") ? looseTasks : looseTasks.slice(0, 5)).map(renderTaskRow)}
                {looseTasks.length > 5 && (
                  <button
                    type="button"
                    className="task-list-item tasks-show-more"
                    aria-label={`${expandedTaskGroups.has("tasks") ? "收起" : "展开"}任务`}
                    aria-expanded={expandedTaskGroups.has("tasks")}
                    onClick={() =>
                      setExpandedTaskGroups((current) => {
                        const next = new Set(current);
                        if (next.has("tasks")) next.delete("tasks");
                        else next.add("tasks");
                        return next;
                      })
                    }
                  >
                    <span className="task-list-title">
                      {expandedTaskGroups.has("tasks") ? "收起显示" : `展开显示（${looseTasks.length - 5}）`}
                    </span>
                  </button>
                )}
              </div>
            </section>
            {loading && tasks.length === 0 && (
              <div className="task-list-state" aria-live="polite">
                <span className="task-list-spinner" aria-hidden="true" />
                <strong>正在读取任务</strong>
                <small>同步最近的对话与运行状态…</small>
              </div>
            )}
            {!loading && tasks.length === 0 && !error && (
              <div className="task-list-state task-list-empty">
                <strong>从第一个任务开始</strong>
                <small>描述目标，Agent 会规划步骤并保留执行记录。</small>
                <button type="button" onClick={onNewTask}>开始新任务</button>
              </div>
            )}
            {error && (
              <div className="task-list-state task-list-error" role="alert">
                <strong>任务列表暂时不可用</strong>
                <small>当前任务不受影响，可以重新连接历史记录。</small>
                <button type="button" onClick={retryTasks}>重新加载</button>
              </div>
            )}
          </div>
          <div className="task-sidebar-account">
            <AccountMenu />
          </div>
        </>
      )}
    </aside>
  );
}
