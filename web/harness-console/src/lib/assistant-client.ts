export type Objective = {
  objectiveId: string; title: string; instructions: string;
  agentName: string; agentVersion: string; projectId: string | null;
  status: "active" | "paused" | "completed" | "cancelled";
  notificationMode: "important" | "all_results" | "none";
  revision: number; schedule: { intervalMinutes: number; timezone: string } | null;
  nextCheckAt: string | null; completionCriteria: string; endsAt: string | null;
};
export type Execution = { executionId: string; threadId: string; runId: string | null;
  status: "pending" | "running" | "succeeded" | "failed" | "cancelled";
  summary: string; createdAt: string };
export type Notice = { itemId: string; objectiveId: string; title: string; content: string;
  kind: string; createdAt: string; readAt: string | null; threadId?: string | null };
export type Specialist = { specialistId: string; name: string; slug: string; description: string;
  agentName: string; agentVersion: string;
  status: "draft" | "needs_input" | "validating" | "ready" | "active" | "paused" | "blocked" | "archived";
  revision: number; config: Record<string, unknown>; createdAt: string; updatedAt: string };
export type Handover = { handoverId: string; specialistId: string; objectiveId: string | null;
  messageId: string; summary: string;
  status: "sent" | "received" | "applied" | "executing" | "pending_input" | "failed";
  createdAt: string; updatedAt: string };
export async function assistantRequest<T>(path: string, method = "GET", body?: unknown, key?: string): Promise<T> {
  const response = await fetch(`/api/studio/assistant/${path}`, {
    method, headers: { "Content-Type": "application/json", ...(key ? { "Idempotency-Key": key } : {}) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }), cache: "no-store",
  });
  if (!response.ok) {
    if (response.status === 401) throw new Error("登录已过期，请重新登录。");
    if (response.status === 403 && path.startsWith("feishu/")) throw new Error("此操作需要工作区管理员权限。");
    if (response.status === 409) {
      // 服务端的冲突理由（缺项、无职责、版本过期…）比通用文案更有用，优先展示。
      const value = await response.json().catch(() => null);
      const message = value?.detail?.message ?? value?.error?.message ?? value?.message ?? (typeof value?.detail === "string" ? value.detail : undefined);
      throw new Error(typeof message === "string" && message ? message : "操作冲突，请刷新后重试。");
    }
    if (response.status === 422) {
      const value = await response.json().catch(() => null);
      const message = value?.error?.message ?? value?.detail?.[0]?.message;
      throw new Error(typeof message === "string" && message ? message : "输入不合法，请检查后重试。");
    }
    if (response.status === 503) throw new Error("助理服务尚未启用，请联系管理员。");
    throw new Error(`操作未完成（${response.status}），请检查输入或稍后重试。`);
  }
  return response.status === 204 ? undefined as T : response.json();
}

export type ManagedProgress = {
  thread_id: string; title: string; status: string; summary: string; active: boolean;
};
export type ManagedPanel = {
  objectiveId: string;
  revision: number;
  budget: Record<string, unknown> | null;
  managed: Array<{
    threadId: string; role: "execute" | "observe"; projectId: string | null;
    dependsOnObjectiveId: string | null; progress: ManagedProgress | null;
  }>;
  overview: ManagedProgress[];
  aggregate: string;
};
