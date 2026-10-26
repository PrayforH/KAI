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
  kind: string; createdAt: string; readAt: string | null };
export async function assistantRequest<T>(path: string, method = "GET", body?: unknown, key?: string): Promise<T> {
  const response = await fetch(`/api/studio/assistant/${path}`, {
    method, headers: { "Content-Type": "application/json", ...(key ? { "Idempotency-Key": key } : {}) },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }), cache: "no-store",
  });
  if (!response.ok) {
    if (response.status === 401) throw new Error("登录已过期，请重新登录。");
    if (response.status === 403 && path.startsWith("feishu/")) throw new Error("此操作需要工作区管理员权限。");
    if (response.status === 409) {
      if (path.startsWith("feishu/")) {
        const value = await response.json().catch(() => null);
        const message = value?.detail?.message ?? value?.error?.message ?? value?.message;
        throw new Error(typeof message === "string" ? message : "飞书配置已变化，请刷新后重试。");
      }
      throw new Error("事项已更新或已有执行，请刷新后重试。");
    }
    if (response.status === 503) throw new Error("助理服务尚未启用，请联系管理员。");
    throw new Error(`操作未完成（${response.status}），请检查输入或稍后重试。`);
  }
  return response.status === 204 ? undefined as T : response.json();
}
