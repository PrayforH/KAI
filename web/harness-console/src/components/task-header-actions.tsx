"use client";
import { useAuth } from "./auth-provider";
import { useRouter } from "next/navigation";
import { CodexTaskIcon } from "./codex-task-icon";
import { CodexResponseIcon } from "./codex-response-icon";
import { ActionMenuPopover } from "./action-menu-popover";
import { writeTextToClipboard } from "../lib/clipboard";
import { RunContextManifest } from "./run-context-manifest";
import { FeedbackToast } from "./feedback-toast";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { NestedMenu } from "./nested-menu";
import { formatTaskAge } from "../lib/task-list-age";
import {
  notifyTaskListChanged,
  setTaskArchived,
  setTaskPinned,
  forkTask,
  setTaskTitle,
  type TaskSummary,
} from "../lib/task-history";
import { useConfirmationDialog } from "./confirmation-dialog";
import { setTaskProject, type ApiProject } from "../lib/studio-client";

const activeStatuses = new Set(["queued", "provisioning", "running", "waiting_approval", "cancelling"]);

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

function BranchIcon() { return <CodexTaskIcon name="branch" />; }

function ProjectIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M3.5 6.5h4l1.4 1.8h7.6v7.2H3.5z" />
    </svg>
  );
}

function DotsIcon() { return <CodexTaskIcon name="more" />; }

function TraceIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M2.8 12.6h2.6l2-5.4 2.6 8 2.2-5.4 1.4 2.8h3.6" />
    </svg>
  );
}

function PinIcon() { return <CodexTaskIcon name="pin" />; }
function RenameIcon() { return <CodexResponseIcon name="pencil" className="task-action-glyph" />; }
function ArchiveIcon() { return <CodexTaskIcon name="archive" />; }

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
          {task.project_id && task.run_id && <RunContextManifest runId={task.run_id} />}
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
  onOpenTrace,
  onRenamed,
  onArchived,
  onProjectChanged,
  variant = "header",
  onChanged,
  onForked,
}: {
  task: TaskSummary;
  variant?: "header" | "row";
  onChanged?: () => void;
  onForked?: (task: TaskSummary) => void;
  /** Projects the task can be moved into; empty hides the project actions. */
  projects?: readonly ApiProject[];
  /** Trace for the run currently shown in this task. */
  observabilityHref?: string | null;
  /** Open the in-app session trace view; falls back to the external link. */
  onOpenTrace?: () => void;
  onRenamed?: (title: string) => void;
  onArchived?: () => void;
  onProjectChanged?: (projectId: string | null) => void;
}) {
  const router = useRouter();
  const { user } = useAuth();
  const [open, setOpen] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const triggerRef = useRef<HTMLButtonElement>(null);
  const close = useCallback(() => setOpen(false), []);
  const containerRef = useDismissable(open && variant === "header", close);
  const { requestConfirmation, confirmationDialog } = useConfirmationDialog();

  const pinned = Boolean(task.pinned_at);
  const isActive = activeStatuses.has(task.status);

  async function run(action: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await action();
      onChanged?.();
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
      notifyTaskListChanged();
      setError("");
      close();
      onProjectChanged?.(projectId);
      onChanged?.();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    } finally {
      setBusy(false);
    }
  }

  const menu = <>
    {variant === "row" && <>
      <button type="button" role="menuitem" disabled={busy} onClick={() => {
        close();
        void writeTextToClipboard(new URL(`/?thread=${encodeURIComponent(task.thread_id)}`, window.location.origin).href).then(copied => {
          if (copied) setNotice("任务链接已复制"); else setError("复制失败，请重试。");
        });
      }}>
        <CodexResponseIcon name="copy" className="task-action-glyph" />
        <span>复制任务链接</span>
      </button>
      <a role="menuitem" href={`/?thread=${encodeURIComponent(task.thread_id)}`} target="_blank" rel="noopener noreferrer" onClick={close}>
        <CodexResponseIcon name="external" className="task-action-glyph" />
        <span>在新标签中打开</span>
      </a>
      <div role="separator" />
    </>}

          {variant === "header" && (<button type="button" role="menuitem" disabled={busy} onClick={togglePinned}>
            <PinIcon />
            <span>{pinned ? "取消置顶任务" : "置顶任务"}</span>
          </button>)}
          <button type="button" role="menuitem" disabled={busy || isActive}
            title={isActive ? "当前任务结束后可创建分支" : undefined}
            onClick={() => void run(async () => {
              const branch = await forkTask(task.thread_id, user.user_id);
              close();
              if (onForked) onForked(branch);
              else router.push(`/?thread=${encodeURIComponent(branch.thread_id)}`);
            })}>
            <CodexTaskIcon name="branch" /><span>分支到新聊天</span>
          </button>
          <button type="button" role="menuitem" disabled={busy} onClick={openRename}>
            <RenameIcon />
            <span>{variant === "row" ? "重命名" : "重命名任务"}</span>
          </button>
          {variant === "row" && (<button type="button" role="menuitem" disabled={busy} onClick={togglePinned}>
            <PinIcon />
            <span>{pinned ? "取消置顶" : "置顶"}</span>
          </button>)}
          {variant === "row" && <div role="separator" />}
          {(projects.length > 0 || task.project_id) && (
            <NestedMenu label={variant === "row" ? "项目" : "移入项目"} icon={<ProjectIcon />} disabled={busy}>
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
          {variant === "row" && <div role="separator" />}
          <button
            type="button"
            role="menuitem"
            disabled={busy || isActive || Boolean(task.archived_at)}
            title={task.archived_at ? "任务已归档" : isActive ? "任务结束后可归档" : undefined}
            onClick={archive}
          >
            <ArchiveIcon />
            <span>{variant === "row" ? "归档" : "归档任务"}</span>
          </button>
          {variant === "row" ? null : onOpenTrace ? (
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                close();
                onOpenTrace();
              }}
            >
              <TraceIcon />
              <span>调用轨迹</span>
            </button>
          ) : observabilityHref ? (
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

  </>;
  return (
    <div className={`task-header-actions${variant === "row" ? " project-task-actions" : ""}`} ref={containerRef}>
      <button
        type="button"
        ref={triggerRef}
        className="header-icon-button task-header-more"
        aria-expanded={open}
        aria-haspopup="menu"
        aria-label={variant === "row" ? `任务操作 ${task.title}` : "更多任务操作"}
        title="更多任务操作"
        onClick={() => setOpen((value) => !value)}
        onKeyDown={(event) => { if (variant === "row" && event.key === "ArrowDown") { event.preventDefault(); setOpen(true); } }}
      >
        <DotsIcon />
      </button>
      <FeedbackToast message={notice} onDismiss={() => setNotice("")} />
      <FeedbackToast message={error} tone="error" onDismiss={() => setError("")} />
      {open && (
        variant === "row" ? <ActionMenuPopover className="task-row-menu" anchor={triggerRef} label={`任务操作 ${task.title}`} onClose={close}>{menu}</ActionMenuPopover> : <div className="task-header-menu" role="menu" aria-label="更多任务操作">{menu}</div>
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
