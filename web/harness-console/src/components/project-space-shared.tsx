"use client";
import Link from "next/link";
import { TaskHeaderActions } from "./task-header-actions";
import { useAuth } from "./auth-provider";
import { useSidebarProjects } from "../lib/sidebar-state";
import type { TaskSummary } from "../lib/task-history";
import { formatTaskAge } from "../lib/task-list-age";
import styles from "./project-home.module.css";
export const ACTIVE = new Set([
  "queued",
  "provisioning",
  "running",
  "waiting_approval",
  "cancelling",
]);
export const STATUS: Record<string, string> = {
  idle: "待开始",
  queued: "排队中",
  provisioning: "准备中",
  running: "执行中",
  waiting_approval: "待确认",
  cancelling: "停止中",
  cancelled: "已取消",
  succeeded: "已完成",
  failed: "执行失败",
  rejected: "已拒绝",
  timed_out: "已超时",
};
export const PAGE_SIZE = 50;
export function errorText(error: unknown) {
  return error instanceof Error ? error.message : "操作失败，请重试。";
}
export function size(bytes: number | null) {
  return !bytes
    ? ""
    : bytes < 1024
      ? `${bytes} B`
      : bytes < 1048576
        ? `${Math.round(bytes / 1024)} KB`
        : `${(bytes / 1048576).toFixed(1)} MB`;
}

export function ProjectTaskRows({
  tasks: items,
  agentLabels,
  compact = false,
  onChanged,
}: {
  tasks: TaskSummary[];
  agentLabels: ReadonlyMap<string, string>;
  compact?: boolean;
  onChanged?: () => void;
}) {
  const { user } = useAuth();
  const [projects] = useSidebarProjects(user.user_id);
  return (
    <ul className={styles.list}>
      {items.map((task) => (
        <li className={compact ? styles.recentRow : styles.row} key={task.thread_id}>
          <span
            className={styles.status}
            data-tone={
              ACTIVE.has(task.status)
                ? "active"
                : task.status === "succeeded"
                  ? "success"
                  : ["failed", "rejected", "timed_out"].includes(task.status)
                    ? "error"
                    : "neutral"
            }
            aria-hidden="true"
          />
          <Link
            className={styles.rowLink}
            href={`/?thread=${encodeURIComponent(task.thread_id)}`}
          >
            <strong>{task.title}{task.pinned_at && <span className={styles.pinnedLabel}>已置顶</span>}</strong>
            {!compact && <small>
              {task.archived_at ? "已归档 · " : ""}
              {task.pending_input
                ? "待补充"
                : (STATUS[task.status] ?? task.status)}{" "}
              ·{" "}
              {agentLabels.get(
                `${task.agent_owner_user_id}:${task.space_id ?? ""}:${task.agent_name}`,
              ) ?? task.agent_name}{" "}
              · {formatTaskAge(task.updated_at)}
            </small>}
          </Link>
          {compact ? <time className={styles.taskTime} dateTime={task.updated_at}>{formatTaskAge(task.updated_at)}</time> : <Link
            className={styles.textLink}
            href={`/?thread=${encodeURIComponent(task.thread_id)}`}
          >
            打开任务 →
          </Link>}
          {compact && <TaskHeaderActions task={task} projects={projects.filter(project => !project.archivedAt)} variant="row" showPinControl onChanged={onChanged} />}
        </li>
      ))}
    </ul>
  );
}
export function projectEmpty(title: string, description: string) {
  return (
    <div className={styles.empty}>
      <strong>{title}</strong>
      <p>{description}</p>
    </div>
  );
}
