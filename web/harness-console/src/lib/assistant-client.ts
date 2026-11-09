export type ObjectiveSchedule = {
  kind?: "once" | "every" | "cron";
  intervalMinutes?: number | null;
  at?: string | null;
  expression?: string | null;
  timezone: string;
  meetingStartsAt?: string | null;
  minutesBefore?: number | null;
};

export type Objective = {
  objectiveId: string; title: string; instructions: string;
  agentName: string; agentVersion: string; projectId: string | null;
  status: "active" | "paused" | "completed" | "cancelled";
  notificationMode: "important" | "all_results" | "none";
  revision: number; schedule: ObjectiveSchedule | null;
  nextCheckAt: string | null; completionCriteria: string; endsAt: string | null;
  specialistId?: string | null; managedThreadId?: string | null; mode?: "autonomous" | "follow_only";
  budget?: Record<string, unknown> | null; parentObjectiveId?: string | null;
};
export type Execution = { executionId: string; threadId: string; runId: string | null;
  status: "pending" | "running" | "succeeded" | "failed" | "cancelled";
  summary: string; createdAt: string };
export type Notice = { itemId: string; objectiveId: string; title: string; content: string;
  kind: string; createdAt: string; readAt: string | null; threadId?: string | null;
  sourceSequence?: number | null;
  // 失败回放：终态 notice 带 Run/执行 id，前端可深链到失败检查点；旧行没有。
  runId?: string | null; executionId?: string | null;
  // 终态 notice 的结算计量（与同次 checkpoint 同口径）；旧行没有这两个字段。
  metrics?: {
    tokens: number | null;
    costUsd: number | null;
    unknownUsage: boolean;
    skippedNoModel?: boolean;
  } | null;
  durationSeconds?: number | null };
export type Specialist = { specialistId: string; name: string; slug: string; description: string;
  activity?: { threadId: string; runId: string | null; status: string };
  agentName: string; agentVersion: string;
  status: "draft" | "needs_input" | "validating" | "ready" | "active" | "paused" | "blocked" | "archived";
  revision: number; config: Record<string, unknown>;
  // 运行态字段在顶层（不在 config）：无人查看暂停与最近查看时间。
  lastViewedAt?: string | null;
  routinePausedReason?: { reason?: string; hours_since_view?: number; at?: string } | null;
  createdAt: string; updatedAt: string };
export type Handover = { handoverId: string; specialistId: string; objectiveId: string | null;
  messageId: string; summary: string;
  status: "sent" | "received" | "applied" | "executing" | "pending_input" | "failed";
  createdAt: string; updatedAt: string };
/**
 * 专用凭据入口：密文入库，config 只写 credential_ref:<名称>，永不回读明文。
 * 返回更新后的 Specialist（revision 已递增，后续 profile 保存要用新 revision）。
 */
export async function saveSpecialistCredential(
  specialistId: string,
  input: { name: string; secret: string; expectedRevision: number },
): Promise<Specialist> {
  return assistantRequest<Specialist>(
    `specialists/${encodeURIComponent(specialistId)}/credentials`,
    "POST",
    {
      name: input.name,
      secret: input.secret,
      expectedRevision: input.expectedRevision,
    },
  );
}

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
      const publicMessage = typeof message === "string" && /^AG-UI thread .* is already bound to /u.test(message)
        ? "这个助理的会话已更新，请重新进入后再发送。" : message;
      throw new Error(typeof publicMessage === "string" && publicMessage ? publicMessage : "操作冲突，请刷新后重试。");
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
