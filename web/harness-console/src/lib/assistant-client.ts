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
  summary: string; createdAt: string;
  /** schedule:/manual:/delegate:/manage: — drives the run-history filter. */
  triggerKey?: string; scheduledFor?: string | null; errorCode?: string | null };
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
    if (response.status === 403) {
      // 飞书沿用固定文案；其它 403（如解散群聊无权限）优先展示服务端原文。
      if (path.startsWith("feishu/")) throw new Error("此操作需要工作区管理员权限。");
      const value = await response.json().catch(() => null);
      const message = value?.detail?.message ?? value?.error?.message ?? value?.message
        ?? (typeof value?.detail === "string" ? value.detail : undefined);
      throw new Error(typeof message === "string" && message ? message : "没有权限执行这个操作。");
    }
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

/** 上传走 multipart，不能再带 Content-Type: application/json。 */
async function assistantFormRequest<T>(path: string, form: FormData): Promise<T> {
  const response = await fetch(`/api/studio/assistant/${path}`, {
    method: "POST", body: form, cache: "no-store",
  });
  if (!response.ok) {
    if (response.status === 401) throw new Error("登录已过期，请重新登录。");
    const value = await response.json().catch(() => null);
    const message = value?.error?.message ?? value?.detail?.message
      ?? (typeof value?.detail === "string" ? value.detail : undefined);
    if (typeof message === "string" && message) throw new Error(message);
    if (response.status === 413) throw new Error("文件超过大小上限，请换一个小一些的文件。");
    throw new Error(`上传未完成（${response.status}），请稍后重试。`);
  }
  return response.json() as Promise<T>;
}

/**
 * 轻量 Skill 闭环：建议来自重复成功任务，保存后仍保持停用，必须显式启用才注入运行。
 */
export type SkillProposalSource = {
  objectiveId: string; executionId: string; runId: string; threadId: string;
  title: string; summary: string; projectId?: string | null;
  specialistId?: string | null; tools?: string[]; finishedAt?: string | null;
};
export type SkillProposal = {
  proposalId: string; status: "proposed" | "draft" | "saved" | "dismissed";
  revision: number; title: string; summary: string;
  sources: SkillProposalSource[];
  draft?: {
    name: string; description: string; instructions: string;
  } | null;
  reply?: string; followUpQuestions?: string[];
  skillId?: string | null; cooldownUntil?: string | null;
  createdAt: string; updatedAt: string;
};
export type PersonalSkill = {
  skillId: string; name: string; displayName: string; description: string;
  enabled: boolean; revision: number; sourceProposalId?: string | null;
  skill: {
    name: string; description: string; instructions: string;
    files?: Array<{ path: string; sizeBytes?: number | null }>;
  };
  createdAt: string; updatedAt: string;
};
export async function listSkillProposals(): Promise<SkillProposal[]> {
  return assistantRequest<SkillProposal[]>("skill-proposals");
}
export async function generateSkillProposal(proposalId: string): Promise<SkillProposal> {
  return assistantRequest<SkillProposal>(
    `skill-proposals/${encodeURIComponent(proposalId)}/generate`, "POST", {},
  );
}
export async function saveSkillProposal(proposalId: string, expectedRevision: number): Promise<PersonalSkill> {
  return assistantRequest<PersonalSkill>(
    `skill-proposals/${encodeURIComponent(proposalId)}/save`, "POST", { expectedRevision },
  );
}
export async function dismissSkillProposal(proposalId: string, expectedRevision: number): Promise<SkillProposal> {
  return assistantRequest<SkillProposal>(
    `skill-proposals/${encodeURIComponent(proposalId)}/dismiss`, "POST", { expectedRevision },
  );
}
export async function listPersonalSkills(): Promise<PersonalSkill[]> {
  return assistantRequest<PersonalSkill[]>("personal-skills");
}
export async function setPersonalSkillEnabled(skillId: string, enabled: boolean, expectedRevision: number): Promise<PersonalSkill> {
  return assistantRequest<PersonalSkill>(
    `personal-skills/${encodeURIComponent(skillId)}/enabled`, "POST", { enabled, expectedRevision },
  );
}
export async function deletePersonalSkill(skillId: string): Promise<{ deleted: boolean }> {
  return assistantRequest<{ deleted: boolean }>(
    `personal-skills/${encodeURIComponent(skillId)}`, "DELETE",
  );
}

/**
 * 群聊：带花名册的共享会话。消息只属于本群时间线；@ 谁谁回复，
 * 未 @ 时由配置的接话人（默认主助理）回复。
 */
export type GroupMemberKind = "creator" | "primary" | "specialist";
export type GroupMember = {
  memberId: string;
  kind: GroupMemberKind;
  name: string;
  specialistId: string | null;
  status: "active" | "removed";
  joinedAt: string;
};
export type AssistantGroup = {
  groupId: string;
  name: string;
  /** 群描述；未填写时服务端整键省略。 */
  description?: string;
  creatorUserId: string;
  primaryPresent: boolean;
  replyAs: string | null;
  lastReadAt: string | null;
  unreadCount: number;
  members: GroupMember[];
  createdAt: string;
  updatedAt: string;
};
export type GroupMessageReply = {
  messageId: string;
  author: "user" | "primary" | "specialist" | "system";
  authorName: string;
  /** 服务端已截断到 200 字。 */
  text: string;
};
export type GroupMessage = {
  messageId: string;
  groupId: string;
  author: "user" | "primary" | "specialist" | "system";
  authorName: string;
  specialistId: string | null;
  text: string;
  runId: string | null;
  replyToMessageId: string | null;
  /** 仅当这条消息显式引用了另一条消息时才出现；机器人自动回链不带。 */
  replyTo?: GroupMessageReply;
  /** 随消息发送的群资料库文件；无附件时服务端整键省略。 */
  attachments?: GroupMessageAttachment[];
  reactions: string[];
  status: "running" | "waiting" | "completed" | "failed" | "stopped";
  createdAt: string;
};
export type GroupMessageAttachment = {
  documentId: string;
  name: string;
  contentType?: string;
  sizeBytes?: number;
  downloadUrl?: string;
};
/** 群资料库：群共享的文档与绑定的知识库。 */
export type GroupLibraryDocument = {
  documentId: string;
  name: string;
  contentType: string;
  sizeBytes: number;
  uploadedBy: string;
  uploadedByName: string;
  createdAt: string;
  downloadUrl: string;
};
export type GroupLibraryKnowledgeBase = {
  reference: string;
  name: string;
  type: string;
  engine: string;
  sourceCount: number;
};
export type AssistantGroupLibrary = {
  documents: GroupLibraryDocument[];
  knowledgeBases: GroupLibraryKnowledgeBase[];
};
export type GroupPostResult = {
  message: GroupMessage;
  replies: GroupMessage[];
  notices: string[];
};

export async function listAssistantGroups(): Promise<AssistantGroup[]> {
  return assistantRequest<AssistantGroup[]>("groups");
}
export async function createAssistantGroup(name: string): Promise<AssistantGroup> {
  return assistantRequest<AssistantGroup>("groups", "POST", { name });
}
export async function addAssistantGroupMember(
  groupId: string,
  specialistId: string,
): Promise<AssistantGroup> {
  return assistantRequest<AssistantGroup>(
    `groups/${encodeURIComponent(groupId)}/members`,
    "POST",
    { specialistId },
  );
}
export async function removeAssistantGroupMember(
  groupId: string,
  memberId: string,
): Promise<AssistantGroup> {
  return assistantRequest<AssistantGroup>(
    `groups/${encodeURIComponent(groupId)}/members/${encodeURIComponent(memberId)}`,
    "DELETE",
  );
}
export async function setAssistantGroupReplyAs(
  groupId: string,
  replyAs: string | null,
): Promise<AssistantGroup> {
  return updateAssistantGroup(groupId, { replyAs });
}
export async function updateAssistantGroup(
  groupId: string,
  fields: { name?: string; description?: string | null; replyAs?: string | null },
): Promise<AssistantGroup> {
  return assistantRequest<AssistantGroup>(
    `groups/${encodeURIComponent(groupId)}`,
    "PATCH",
    fields,
  );
}
export async function deleteAssistantGroup(groupId: string): Promise<void> {
  return assistantRequest<void>(
    `groups/${encodeURIComponent(groupId)}`,
    "DELETE",
  );
}
export async function listAssistantGroupMessages(
  groupId: string,
): Promise<GroupMessage[]> {
  return assistantRequest<GroupMessage[]>(
    `groups/${encodeURIComponent(groupId)}/messages`,
  );
}
export async function postAssistantGroupMessage(
  groupId: string,
  text: string,
  clientMessageId: string,
  replyToMessageId?: string | null,
  attachmentIds?: string[],
): Promise<GroupPostResult> {
  return assistantRequest<GroupPostResult>(
    `groups/${encodeURIComponent(groupId)}/messages`,
    "POST",
    {
      text,
      clientMessageId,
      replyToMessageId: replyToMessageId ?? undefined,
      attachmentIds: attachmentIds?.length ? attachmentIds : undefined,
    },
  );
}
export async function deleteAssistantGroupMessage(
  groupId: string,
  messageId: string,
): Promise<void> {
  return assistantRequest<void>(
    `groups/${encodeURIComponent(groupId)}/messages/${encodeURIComponent(messageId)}`,
    "DELETE",
  );
}
export async function getAssistantGroupLibrary(
  groupId: string,
): Promise<AssistantGroupLibrary> {
  return assistantRequest<AssistantGroupLibrary>(
    `groups/${encodeURIComponent(groupId)}/library`,
  );
}
export async function uploadAssistantGroupDocument(
  groupId: string,
  file: File,
): Promise<GroupLibraryDocument> {
  const form = new FormData();
  form.append("file", file);
  return assistantFormRequest<GroupLibraryDocument>(
    `groups/${encodeURIComponent(groupId)}/library/documents`,
    form,
  );
}
export async function deleteAssistantGroupDocument(
  groupId: string,
  documentId: string,
): Promise<void> {
  return assistantRequest<void>(
    `groups/${encodeURIComponent(groupId)}/library/documents/${encodeURIComponent(documentId)}`,
    "DELETE",
  );
}
export async function bindAssistantGroupKnowledgeBase(
  groupId: string,
  reference: string,
): Promise<void> {
  return assistantRequest<void>(
    `groups/${encodeURIComponent(groupId)}/library/knowledge-bases`,
    "POST",
    { reference },
  );
}
export async function unbindAssistantGroupKnowledgeBase(
  groupId: string,
  reference: string,
): Promise<void> {
  return assistantRequest<void>(
    `groups/${encodeURIComponent(groupId)}/library/knowledge-bases/${encodeURIComponent(reference)}`,
    "DELETE",
  );
}
export async function markAssistantGroupRead(
  groupId: string,
): Promise<AssistantGroup> {
  return assistantRequest<AssistantGroup>(
    `groups/${encodeURIComponent(groupId)}/read`,
    "POST",
    {},
  );
}
export async function toggleAssistantGroupReaction(
  groupId: string,
  messageId: string,
  emoji: string,
): Promise<{ reactions: string[] }> {
  return assistantRequest<{ reactions: string[] }>(
    `groups/${encodeURIComponent(groupId)}/messages/${encodeURIComponent(messageId)}/reactions`,
    "POST",
    { emoji },
  );
}
