import type { RunViewModel } from "./run-view-model";

export interface CompactionProgress {
  key: string;
  state: "running" | "completed" | "failed" | "interrupted";
  label: string;
}

export function compactionProgress(view: RunViewModel): CompactionProgress | null {
  const boundaries = view.items.filter((item) =>
    ["context.compaction.started", "context.compaction.completed", "context.compacted", "context.compaction.failed"].includes(item.event_type)
    || (item.event_type === "runtime.system" && item.title === "正在压缩长上下文"),
  );
  let boundary = boundaries.at(-1);
  // A later durable checkpoint must not restart the same success notification.
  if (boundary?.event_type === "context.compacted" && boundaries.at(-2)?.event_type === "context.compaction.completed") {
    boundary = boundaries.at(-2);
  }
  if (!boundary) return null;
  const key = `${view.runId}:${boundary.id}`;
  if (boundary.event_type === "context.compaction.failed") {
    return { key, state: "failed", label: "上下文压缩失败" };
  }
  if (["context.compacted", "context.compaction.completed"].includes(boundary.event_type)) {
    return { key, state: "completed", label: "上下文已压缩" };
  }
  if (!["queued", "running", "waiting_approval"].includes(view.phase)) {
    return { key, state: "interrupted", label: "上下文压缩未完成" };
  }
  return { key, state: "running", label: "正在自动压缩上下文…" };
}
