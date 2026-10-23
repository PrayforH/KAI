"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { forkTask, type TaskSummary } from "../lib/task-history";
import { CodexTaskIcon } from "./codex-task-icon";
import { FeedbackToast } from "./feedback-toast";

type BranchScope = { userId: string; threadId: string };

/** Historical turns keep their timestamp; only the latest completed task can fork. */
export function TurnCompletionControl({ completedAt, branchScope, onForked }: {
  completedAt: string;
  branchScope?: BranchScope | null;
  onForked?: (task: TaskSummary) => void;
}) {
  const date = new Date(completedAt);
  if (!Number.isFinite(date.getTime())) return null;
  const time = <time className="turn-completion" dateTime={completedAt} title={date.toLocaleString("zh-CN")}>
    {date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit", hour12: false })}
  </time>;
  return branchScope ? <BranchCompletion scope={branchScope} onForked={onForked}>{time}</BranchCompletion> : time;
}

function BranchCompletion({ scope, children, onForked }: { scope: BranchScope; children: ReactNode; onForked?: (task: TaskSummary) => void }) {
  const router = useRouter();
  const pending = useRef(false);
  const mounted = useRef(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  async function branch() {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError("");
    try {
      const task = await forkTask(scope.threadId, scope.userId);
      if (mounted.current) {
        if (onForked) onForked(task);
        else router.push(`/?thread=${encodeURIComponent(task.thread_id)}`);
      }
    } catch (cause) {
      if (mounted.current) setError(cause instanceof Error ? cause.message : "创建分支失败，请重试。");
    } finally {
      pending.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  return <span className="turn-completion-slot" data-branch-pending={busy || undefined}>
    <button className="turn-branch-button" type="button" aria-label="分支到新任务" title="分支到新任务"
      aria-busy={busy} disabled={busy} onClick={() => void branch()}>
      <CodexTaskIcon name="branchAlt" />
    </button>
    {children}
    <FeedbackToast message={error} tone="error" onDismiss={() => setError("")} />
  </span>;
}
