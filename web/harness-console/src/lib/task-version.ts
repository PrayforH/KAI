import { requireAuthenticatedResponse } from "./client-auth";
import { notifyTaskListChanged } from "./task-history";
import type { RunStreamSnapshot } from "./run-stream-store";
import type { RunViewModel } from "./run-view-model";

export function taskVersionBusy(stream: RunStreamSnapshot, view?: Pick<RunViewModel, "runId" | "phase">): boolean {
  // A terminal snapshot settles this run even if its SSE connection is still closing.
  const settled = view && ["completed", "failed", "rejected", "cancelled"].includes(view.phase);
  if (settled && stream.runId === view.runId) return false;
  return stream.status === "running" || Boolean(view && ["queued", "running", "waiting_approval"].includes(view.phase));
}

export async function persistTaskVersion(threadId: string, version: string): Promise<void> {
  const response = requireAuthenticatedResponse(await fetch(
    `/api/agui/threads/${encodeURIComponent(threadId)}/version`,
    { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ agent_version: version }) },
  ));
  if (!response.ok) {
    const message = response.status === 409
      ? "任务仍有未结束的运行，暂时不能切换版本，请稍后重试。"
      : `版本切换失败（HTTP ${response.status}），原版本已保留。`;
    throw new Error(message);
  }
  notifyTaskListChanged();
}
