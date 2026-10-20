"use client";

import { isFailedRunStatus } from "../lib/run-status";
import { CodexTaskIcon } from "./codex-task-icon";
import { TaskHeaderActions } from "./task-header-actions";
import { SidebarIcon } from "./sidebar-icon";
import Link from "next/link";
import { projectHref } from "../lib/project-space";
import { ProjectCreateDialog } from "./project-create-dialog";
import { useConfirmationDialog } from "./confirmation-dialog";
import { PanelResizeHandle } from "./panel-resize-handle";
import { useEffect, useId, useMemo, useRef, useState, type ReactNode } from "react";
import { AccountMenu } from "./account-menu";
import { useAuth } from "./auth-provider";
import { ProductBrandMark, ProductWordmark, PRODUCT_NAME } from "./product-brand";
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
  setTaskPinned,
  type TaskSummary,
} from "../lib/task-history";
import { formatTaskAge } from "../lib/task-list-age";
import { agentDisplayName } from "../lib/agent-display-name";
import {
  ApiProject,
  projectClient,
} from "../lib/studio-client";
import { useSidebarGroups } from "../lib/sidebar-state";
import { taskListRefreshDelay } from "../lib/task-list-refresh";


const statusLabels: Record<string, string> = {
  idle: "新任务",
  queued: "排队中",
  running: "运行中",
  waiting_approval: "待审批",
  waiting_input: "需要输入",
  cancelling: "取消中",
  cancelled: "已取消",
  succeeded: "已完成",
  failed: "处理错误",
  rejected: "已拒绝",
  timed_out: "已超时",
};

function NewTaskIcon() { return <SidebarIcon name="compose" className="task-new-icon" />; }
function AddToProjectIcon() { return <SidebarIcon name="plus" />; }
function SidebarCollapseIcon() { return <SidebarIcon name="panel" />; }
function ProjectFolderIcon() { return <SidebarIcon name="folder" className="task-project-folder" />; }

function MoreIcon() { return <CodexTaskIcon name="more" />; }

function PinIcon() { return <CodexTaskIcon name="pin" />; }

function TrashIcon() {
  return (
    <svg viewBox="0 0 16 16" aria-hidden="true">
      <path d="M3.5 4.5h9M6 4.5V3h4v1.5M4.5 4.5l.6 8.2h5.8l.6-8.2" />
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
      // Reveal the clipped part at 80px per second; CSS holds the final
      // position until the pointer or keyboard focus leaves the row.
      viewport.style.setProperty("--task-title-duration", `${Math.max(0.8, overflow / 80)}s`);
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
}: {
  currentThreadId: string;
  collapsed: boolean;
  overlayOpen?: boolean;
  onToggle: () => void;
  onSelect: (task: TaskSummary) => void;
  onNewTask: () => void;
  /** Persist a new task in this project, including when it has no tasks yet. */
  onNewTaskWithProject?: (project: ApiProject) => Promise<void>;
  searchControl?: ReactNode;
  /** Which workspace nav item is highlighted; defaults to the task page. */
  activeNav?: WorkspaceId;
  /** Optional agent_name -> display label map; falls back to platform names. */
  agentLabels?: Readonly<Record<string, string>>;
  /** Projects shown above the plain task list, in creation order. */
  projects?: readonly ApiProject[];
  onProjectsChanged?: () => void;
}) {
  // Seed from the shared snapshot so navigating to a Studio page and back
  // (a fresh mount) renders the previous list immediately instead of
  // clearing it behind the loading state.
  const [tasks, setTasks] = useState<TaskSummary[]>(() => peekCachedTasks() ?? []);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(() => peekCachedTasks() === null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [updatingThreadId, setUpdatingThreadId] = useState("");
  const [creatingProjectId, setCreatingProjectId] = useState<string | null>(null);
  const [projectCreateError, setProjectCreateError] = useState("");
  const [projectDialogOpen, setProjectDialogOpen] = useState(false);
  const [projectMenuId, setProjectMenuId] = useState<string | null>(null);
  const [pinningProjectId, setPinningProjectId] = useState("");
  const projectCreationPending = useRef(false);
  const { requestConfirmation, confirmationDialog } = useConfirmationDialog();
  const sidebarRef = useRef<HTMLElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const expandButtonRef = useRef<HTMLButtonElement>(null);
  const wasOverlayOpenRef = useRef(false);
  const runView = useRunViewModel();
  const { user } = useAuth();
  const [expandedTaskGroups, setExpandedTaskGroups] = useSidebarGroups(user.user_id);
  const sectionId = useId();
  const projectsCollapsed = expandedTaskGroups.has("section:projects:collapsed");
  const tasksCollapsed = expandedTaskGroups.has("section:tasks:collapsed");
  function toggleSection(section: "projects" | "tasks") {
    setExpandedTaskGroups((current) => {
      const next = new Set(current);
      const key = `section:${section}:collapsed`;
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  useEffect(() => {
    if (!projectMenuId) return;
    const dismiss = (event: PointerEvent) => {
      const target = event.target;
      if (
        target instanceof Element
        && target.closest(`[data-project-menu="${projectMenuId}"]`)
      ) return;
      setProjectMenuId(null);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setProjectMenuId(null);
    };
    document.addEventListener("pointerdown", dismiss);
    window.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", dismiss);
      window.removeEventListener("keydown", escape);
    };
  }, [projectMenuId]);


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
    open: overlayOpen && !projectDialogOpen,
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
      // Replace an in-flight pre-mutation read instead of dropping the refresh.
      setRefreshKey((current) => current + 1);
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

  async function toggleTaskPin(task: TaskSummary) {
    if (updatingThreadId) return;
    setUpdatingThreadId(task.thread_id);
    try {
      const pinned = !task.pinned_at;
      await setTaskPinned(task.thread_id, pinned);
      setTasks(current => current.map(item => item.thread_id === task.thread_id
        ? { ...item, pinned_at: pinned ? new Date().toISOString() : null } : item));
      setError("");
      notifyTaskListChanged();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally { setUpdatingThreadId(""); }
  }

  const selected = useMemo(
    () => tasks.find((task) => task.thread_id === currentThreadId),
    [currentThreadId, tasks],
  );
  const previousSelectedThread = useRef(currentThreadId);
  useEffect(() => {
    const changed = previousSelectedThread.current !== currentThreadId;
    previousSelectedThread.current = currentThreadId;
    if (!selected?.project_id) return;
    const key = `project:${selected.project_id}`;
    const hiddenKey = `collapsed:${selected.project_id}`;
    setExpandedTaskGroups((current) => {
      // Restore a deliberate collapse on remount; selecting another task can
      // still reveal its containing folder.
      if (!changed && current.has(hiddenKey)) return current;
      if (current.has(key)) return current;
      const next = new Set(current).add(key);
      next.delete(hiddenKey);
      return next;
    });
  }, [currentThreadId, selected?.project_id, setExpandedTaskGroups]);
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
    if (task.pending_input && ["succeeded", "completed"].includes(status)) status = "waiting_input";
    const needsInput = status === "waiting_input";
    const hasError = isFailedRunStatus(status);
    const statusLabel = statusLabels[status] ?? status;
    const unreadResult =
      status === "succeeded" &&
      task.thread_id !== currentThreadId &&
      !isTaskRead(task);
    const showStatusDot = !needsInput && (activeStatuses.has(status) || hasError || unreadResult);
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
          title={needsInput ? "需要你补充信息，点击任务继续回答" : hasError ? `${statusLabel}，点击查看详情` : undefined}
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
          {needsInput && <span className="task-input-badge">需要输入</span>}
          {hasError && <span className="task-error-label">{statusLabel}</span>}
          <span className="task-list-meta">
            <time dateTime={task.updated_at}>{formatTaskAge(task.updated_at)}</time>
          </span>
        </button>
        <div className="task-row-actions">
          <button type="button" className={`task-list-pin${task.pinned_at ? " is-pinned" : ""}`}
            onClick={() => void toggleTaskPin(task)} disabled={Boolean(updatingThreadId)}
            aria-label={`${task.pinned_at ? "取消置顶" : "置顶"} ${task.title}`}
            aria-pressed={Boolean(task.pinned_at)} title={task.pinned_at ? "取消置顶" : "置顶"}>
            <CodexTaskIcon name="pin" />
          </button>
          <TaskHeaderActions task={task} variant="row" projects={projects}
            onForked={onSelect} onArchived={() => { if (task.thread_id === currentThreadId) onNewTask(); }} />
        </div>
      </div>
    );
  }

  async function toggleProjectPin(project: ApiProject) {
    if (pinningProjectId) return;
    setProjectMenuId(null);
    setPinningProjectId(project.projectId);
    setProjectCreateError("");
    try {
      await projectClient.update(project.projectId, {
        pinned: !project.pinnedAt,
      });
      onProjectsChanged?.();
    } catch (cause) {
      setProjectCreateError(
        cause instanceof Error ? cause.message : "更新项目失败，请重试",
      );
    } finally {
      setPinningProjectId("");
    }
  }

  async function removeProject(project: ApiProject) {
    setProjectMenuId(null);
    const confirmed = await requestConfirmation({
      title: `移除项目「${project.name}」？`,
      description:
        "项目会从侧边栏移除，项目里的任务会回到「任务」区，历史记录不会删除。",
      confirmLabel: "移除项目",
      tone: "danger",
    });
    if (!confirmed) return;
    try {
      await projectClient.remove(project.projectId);
      onProjectsChanged?.();
      notifyTaskListChanged();
    } catch (cause) {
      setProjectCreateError(
        cause instanceof Error ? cause.message : "移除项目失败，请重试",
      );
    }
  }

  async function startProjectTask(project: ApiProject) {
    if (!onNewTaskWithProject || projectCreationPending.current) return;
    projectCreationPending.current = true;
    setCreatingProjectId(project.projectId);
    setProjectCreateError("");
    try {
      await onNewTaskWithProject(project);
      setExpandedTaskGroups((current) => new Set(current).add(`project:${project.projectId}`));
    } catch (cause) {
      setProjectCreateError(cause instanceof Error ? cause.message : "新建任务失败，请重试");
    } finally {
      projectCreationPending.current = false;
      setCreatingProjectId(null);
    }
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
                <ProductWordmark />
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
          <div className="task-list-scroll">
          <div className="task-sidebar-mode">
            <WorkspaceNavigation
              active={activeNav}
              visible={["knowledge", "agents", "automation", "skills"]}
            />
          </div>
          <div className="task-list-toolbar task-project-toolbar">
            <div className="task-list-heading">
              <button type="button" className="task-section-toggle"
                aria-label={`${projectsCollapsed ? "展开" : "收起"}项目分组`}
                aria-expanded={!projectsCollapsed} aria-controls={`${sectionId}-projects`}
                onClick={() => toggleSection("projects")}>
                <span>项目</span>
                <SidebarIcon name="chevronDown" size={12} className="task-group-chevron" />
              </button>
            </div>
            <button
              type="button"
              className="task-project-create"
              aria-label="新建项目"
              title="新建项目"
              onClick={() => setProjectDialogOpen(true)}
            >
              <AddToProjectIcon />
            </button>
          </div>
          {!projectsCollapsed && <div className="task-list" role="list" id={`${sectionId}-projects`}>
            {projectCreateError && <p className="task-project-create-error" role="alert">{projectCreateError}</p>}
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
                          if (next.has(key)) {
                            next.delete(key);
                            next.add(`collapsed:${project.projectId}`);
                          } else {
                            next.add(key);
                            next.delete(`collapsed:${project.projectId}`);
                          }
                          return next;
                        });
                      }}
                    >
                      <ProjectFolderIcon />
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
                    </button>
                    <div className="task-project-menu-anchor" data-project-menu={project.projectId}>
                      <button
                        type="button"
                        className="task-project-settings"
                        aria-label={`项目操作 ${project.name}`}
                        title={`项目操作 ${project.name}`}
                        aria-haspopup="menu"
                        aria-expanded={projectMenuId === project.projectId}
                        disabled={pinningProjectId === project.projectId}
                        onClick={() => setProjectMenuId((current) =>
                          current === project.projectId ? null : project.projectId
                        )}
                      >
                        <MoreIcon />
                      </button>
                      {projectMenuId === project.projectId && (
                        <div
                          className="task-project-menu"
                          role="menu"
                          aria-label={`项目操作 ${project.name}`}
                          data-project-menu={project.projectId}
                        >
                          <Link role="menuitem" href={projectHref(project.projectId)} onClick={() => setProjectMenuId(null)}>
                            <ProjectFolderIcon /><span>查看项目</span>
                          </Link>
                          <button
                            type="button"
                            role="menuitem"
                            onClick={() => void toggleProjectPin(project)}
                          >
                            <PinIcon />
                            <span>{project.pinnedAt ? "取消置顶" : "置顶"}</span>
                          </button>
                          <button
                            type="button"
                            role="menuitem"
                            className="is-danger"
                            onClick={() => void removeProject(project)}
                          >
                            <TrashIcon />
                            <span>移除项目</span>
                          </button>
                        </div>
                      )}
                    </div>
                    <button
                        type="button"
                        className="task-project-add"
                        aria-label={`在 ${project.name} 下新建任务`}
                        title="新建项目任务"
                        disabled={!onNewTaskWithProject || creatingProjectId !== null}
                        aria-busy={creatingProjectId === project.projectId}
                        onClick={() => void startProjectTask(project)}
                      >
                        <AddToProjectIcon />
                    </button>
                  </div>
                  {!collapsed && (
                    <div className="task-project-items">
                      {projectTasks.length === 0 && (
                        <p className="task-project-empty">还没有任务，点击右侧 ＋ 新建</p>
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
          </div>}
          <div className="task-list-toolbar task-agent-section-heading">
            <div className="task-list-heading">
              <button type="button" className="task-section-toggle"
                aria-label={`${tasksCollapsed ? "展开" : "收起"}任务分组`}
                aria-expanded={!tasksCollapsed} aria-controls={`${sectionId}-tasks`}
                onClick={() => toggleSection("tasks")}>
                <span>任务</span>
                <SidebarIcon name="chevronDown" size={12} className="task-group-chevron" />
              </button>
            </div>
          </div>
          {!tasksCollapsed && <div className="task-list" role="list" id={`${sectionId}-tasks`}>
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
          </div>}
          </div>
          <div className="task-sidebar-account">
            <AccountMenu />
          </div>
        </>
      )}
      {projectDialogOpen && <ProjectCreateDialog
        onClose={() => setProjectDialogOpen(false)}
        onCreated={(project) => {
          setExpandedTaskGroups((current) => new Set(current).add(`project:${project.projectId}`));
          onProjectsChanged?.();
        }}
      />}
      {confirmationDialog}
    </aside>
  );
}
