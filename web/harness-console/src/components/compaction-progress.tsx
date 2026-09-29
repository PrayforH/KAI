"use client";

import { useEffect, useState } from "react";
import { compactionProgress } from "../lib/compaction-progress";
import type { RunViewModel } from "../lib/run-view-model";

export function CompactionProgressIndicator({ view }: { view: RunViewModel }) {
  const progress = compactionProgress(view);
  const [dismissed, setDismissed] = useState<string | null>(null);
  const active = ["queued", "running", "waiting_approval"].includes(view.phase);
  useEffect(() => {
    if (progress?.state !== "completed" || !active) return;
    const key = progress.key;
    const timer = window.setTimeout(() => setDismissed(key), 2500);
    return () => window.clearTimeout(timer);
  }, [progress?.key, progress?.state, active]);
  if (!progress || (progress.state === "completed" && (!active || dismissed === progress.key))) return null;
  return <div className={`execution-compaction is-${progress.state}`} role="status" aria-live="polite" aria-atomic="true">
    <span className="execution-compaction-icon" aria-hidden="true">
      {progress.state === "completed" ? "✓" : progress.state === "running" ? "" : "!"}
    </span>
    <span>{progress.label}</span>
  </div>;
}
