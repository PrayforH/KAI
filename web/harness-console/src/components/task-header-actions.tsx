"use client";
import { FeedbackToast } from "./feedback-toast";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { NestedMenu } from "./nested-menu";
import { formatTaskAge } from "../lib/task-list-age";
import {
  notifyTaskListChanged,
  setTaskArchived,
  setTaskPinned,
  setTaskTitle,
  type TaskSummary,
} from "../lib/task-history";
import { useConfirmationDialog } from "./confirmation-dialog";
import { setTaskProject, type ApiProject } from "../lib/studio-client";

const activeStatuses = new Set(["queued", "running", "waiting_approval", "cancelling"]);

function ProjectFolderIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M2.75 5.75a2 2 0 0 1 2-2h3.1l1.7 1.9h5.7a2 2 0 0 1 2 2v6.5a2 2 0 0 1-2 2H4.75a2 2 0 0 1-2-2Z" />
    </svg>
  );
}

function RecentActivityIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <circle cx="10" cy="10" r="6.75" />
      <path d="M10 6.4V10l2.6 1.6" />
    </svg>
  );
}

function BranchIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <circle cx="6" cy="5" r="1.9" />
      <circle cx="6" cy="15" r="1.9" />
      <circle cx="14" cy="7.5" r="1.9" />
      <path d="M6 6.9v6.2M14 9.4c0 2.5-1.8 3.4-4.2 3.7-1.2.15-2.4.4-3.1 1" />
    </svg>
  );
}

function ProjectIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M3.5 6.5h4l1.4 1.8h7.6v7.2H3.5z" />
    </svg>
  );
}

function DotsIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <circle cx="4.4" cy="10" r="1.3" />
      <circle cx="10" cy="10" r="1.3" />
      <circle cx="15.6" cy="10" r="1.3" />
    </svg>
  );
}

function TraceIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M2.8 12.6h2.6l2-5.4 2.6 8 2.2-5.4 1.4 2.8h3.6" />
    </svg>
  );
}

function PinIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="m11.5 3 5.5 5.5-2.4.7-2.9 2.9-.3 3-5.9-5.9 3-.3 2.9-2.9ZM6.4 13.6 3 17" />
    </svg>
  );
}

function RenameIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M8 13.2 8.5 11l6.8-6.8a1.4 1.4 0 0 1 2 2L10.5 13Z" />
      <path d="M3.5 16.5h13" />
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

function useDismissable(open: boolean, close: () => void) {
  const containerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: PointerEvent) {
      if (!containerRef.current?.contains(event.target as Node)) close();
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") close();
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open, close]);
  return containerRef;
}

/**
 * Header folder trigger: one bare folder icon before the task title. Hover or
 * click opens the task card (name, project folder, activity) and — when the
 * task runs a switchable agent — the version switcher folded inside.
 */
export function TaskDetailsPopover({
  task,
  switcher,
}: {
  task: TaskSummary;
  switcher?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const hoverTimer = useRef<number>(0);
  const closeTimer = useRef<number>(0);
  const close = useCallback(() => setOpen(false), []);
  const containerRef = useDismissable(open, close);

  const armHover = () => {
    window.clearTimeout(hoverTimer.current);
    window.clearTimeout(closeTimer.current);
    hoverTimer.current = window.setTimeout(() => setOpen(true), 220);
  };
  // Leaving the wrapper schedules a short close so crossing the popover gap
  // (the trigger and the popover are 8px apart) does not flicker the card.
  const scheduleClose = () => {
    window.clearTimeout(hoverTimer.current);
    window.clearTimeout(closeTimer.current);
    closeTimer.current = window.setTimeout(() => setOpen(false), 180);
  };
  const cancelClose = () => window.clearTimeout(closeTimer.current);
  useEffect(() => () => {
    window.clearTimeout(hoverTimer.current);
    window.clearTimeout(closeTimer.current);
  }, []);

  const age = formatTaskAge(task.updated_at);

  return (
    <div
      className="task-details"
      ref={containerRef}
      onPointerEnter={(event) => {
        if (event.pointerType === "mouse") {
          armHover();
          cancelClose();
        }
      }}
      onPointerLeave={(event) => {
        if (event.pointerType === "mouse") scheduleClose();
      }}
    >
      <button
        type="button"
        className="task-folder-trigger"
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-label="任务详情"
        title="任务详情"
        onClick={() => setOpen((value) => !value)}
      >
        <ProjectFolderIcon />
      </button>
      {open && (
        <div className="task-details-popover" role="dialog" aria-label="任务详情">
          <div className="task-details-row is-folder">
            <ProjectFolderIcon />
            <strong>{task.title}</strong>
          </div>
          <div className="task-details-row is-path">
            <span>{task.agent_name}</span>
          </div>
          <div className="task-details-row is-activity">
            <RecentActivityIcon />
            <span>最近活动 {age === "刚刚" ? "刚刚" : `${age}前`}</span>
          </div>
          {switcher ? (
            <div className="task-details-row is-branch is-switcher">{switcher}</div>
          ) : (
            <div className="task-details-row is-branch">
              <BranchIcon />
              <span>{task.agent_version}</span>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function RenameTaskDialog({
  open,
  initialTitle,
  onCancel,
  onSubmit,
}: {
  open: boolean;
  initialTitle: string;
  onCancel: () => void;
  onSubmit: (title: string) => void;
}) {
  const [value, setValue] = useState(initialTitle);
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!open) return;
    setValue(initialTitle);
    const timer = window.setTimeout(() => {
      inputRef.current?.focus();
      inputRef.current?.select();
    }, 20);
    return () => window.clearTimeout(timer);
  }, [open, initialTitle]);
  if (!open) return null;
  const trimmed = value.trim();
  return createPortal(
    <div
      className="task-rename-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onCancel();
      }}
    >
      <form
        className="task-rename-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="重命名任务"
        onSubmit={(event) => {
          event.preventDefault();
          if (trimmed && trimmed !== initialTitle) onSubmit(trimmed);
          else onCancel();
        }}
      >
        <strong>重命名任务</strong>
        <input
          ref={inputRef}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          maxLength={200}
          required
          aria-label="任务名称"
          onKeyDown={(event) => {
            if (event.key === "Escape") onCancel();
          }}
        />
        <div className="task-rename-actions">
          <button type="button" onClick={onCancel}>取消</button>
          <button type="submit" disabled={!trimmed || trimmed === initialTitle}>确认</button>
        </div>
      </form>
    </div>,
    document.body,
  );
}

/**
 * The header "..." menu: pin, rename and archive for the current durable task.
 */
export function TaskHeaderActions({
  task,
  projects = [],
  observabilityHref,
  onRenamed,
  onArchived,
  onProjectChanged,
}: {
  task: TaskSummary;
  /** Projects the task can be moved into; empty hides the project actions. */
  projects?: readonly ApiProject[];
  /** Trace for the run currently shown in this task. */
  observabilityHref?: string | null;
  onRenamed?: (title: string) => void;
  onArchived?: () => void;
  onProjectChanged?: (projectId: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const close = useCallback(() => setOpen(false), []);
  const containerRef = useDismissable(open, close);
  const { requestConfirmation, confirmationDialog } = useConfirmationDialog();

  const pinned = Boolean(task.pinned_at);
  const isActive = activeStatuses.has(task.status);

  async function run(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await action();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  const togglePinned = () => {
    const next = !pinned;
    void run(async () => {
      await setTaskPinned(task.thread_id, next);
      notifyTaskListChanged();
    });
    close();
  };

  const openRename = () => {
    close();
    setRenameOpen(true);
  };

  const rename = (title: string) => {
    void run(async () => {
      await setTaskTitle(task.thread_id, title);
      onRenamed?.(title);
      notifyTaskListChanged();
    });
    setRenameOpen(false);
  };

  const archive = () => {
    close();
    void (async () => {
      const confirmed = await requestConfirmation({
        title: "归档任务",
        description: `“${task.title}”将从任务列表移除，可随时在已归档任务中找回。`,
        confirmLabel: "归档",
      });
      if (!confirmed) return;
      await run(async () => {
        await setTaskArchived(task.thread_id, true);
        notifyTaskListChanged();
        onArchived?.();
      });
    })();
  };

  /** Move the task into a project, or out of the current one. */
  async function moveToProject(projectId: string | null) {
    setBusy(true);
    try {
      await setTaskProject(task.thread_id, projectId);
      setError("");
      close();
      onProjectChanged?.(projectId);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="task-header-actions" ref={containerRef}>
      <button
        type="button"
        className="header-icon-button task-header-more"
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label="更多任务操作"
        title="更多任务操作"
        onClick={() => setOpen((value) => !value)}
      >
        <DotsIcon />
      </button>
      <FeedbackToast message={error} tone="error" onDismiss={() => setError("")} />
      {open && (
        <div className="task-header-menu" role="menu" aria-label="更多任务操作">
          {observabilityHref ? (
            <a
              role="menuitem"
              href={observabilityHref}
              target="_blank"
              rel="noopener noreferrer"
              onClick={close}
            >
              <TraceIcon />
              <span>调用轨迹</span>
            </a>
          ) : (
            <span className="task-header-menu-unavailable" aria-disabled="true">
              <TraceIcon />
              <span>调用轨迹</span>
            </span>
          )}
          <button type="button" role="menuitem" disabled={busy} onClick={togglePinned}>
            <PinIcon />
            <span>{pinned ? "取消置顶任务" : "置顶任务"}</span>
          </button>
          <button type="button" role="menuitem" disabled={busy} onClick={openRename}>
            <RenameIcon />
            <span>重命名任务</span>
          </button>
          {(projects.length > 0 || task.project_id) && (
            <NestedMenu label="移入项目" icon={<ProjectIcon />} disabled={busy}>
              {projects
                .filter((project) => project.projectId !== task.project_id)
                .map((project) => (
                  <button
                    key={project.projectId}
                    type="button"
                    role="menuitem"
                    disabled={busy}
                    onClick={() => void moveToProject(project.projectId)}
                  >
                    <ProjectIcon />
                    <span>{project.name}</span>
                  </button>
                ))}
              {task.project_id && (
                <button
                  type="button"
                  role="menuitem"
                  disabled={busy}
                  onClick={() => void moveToProject(null)}
                >
                  <ProjectIcon />
                  <span>移出当前项目</span>
                </button>
              )}
            </NestedMenu>
          )}
          <button
            type="button"
            role="menuitem"
            disabled={busy || isActive}
            title={isActive ? "任务结束后可归档" : undefined}
            onClick={archive}
          >
            <ArchiveIcon />
            <span>归档任务</span>
          </button>
        </div>
      )}
      <RenameTaskDialog
        open={renameOpen}
        initialTitle={task.title}
        onCancel={() => setRenameOpen(false)}
        onSubmit={rename}
      />
      {confirmationDialog}
    </div>
  );
}
