import { requireAuthenticatedResponse } from "./client-auth";
import { projectClient } from "./studio-client";
import type { TaskSummary } from "./task-history";

export type ProjectTab = "overview" | "tasks" | "resources" | "results";
export function projectTab(value?: string | null): ProjectTab {
  return ["tasks", "resources", "results"].includes(value ?? "")
    ? (value as ProjectTab)
    : "overview";
}
export function projectHref(projectId: string, tab: ProjectTab = "overview") {
  return `/studio/projects/${encodeURIComponent(projectId)}${tab === "overview" ? "" : `?tab=${tab}`}`;
}

export interface ProjectArtifact {
  artifact_id: string;
  name: string;
  media_type: string;
  size_bytes: number | null;
  thread_id: string;
  thread_title: string;
  agent_name: string;
  created_at: string;
  task_archived?: boolean;
}

async function read<T>(url: string, notFoundValue?: T): Promise<T> {
  const response = requireAuthenticatedResponse(
    await fetch(url, { cache: "no-store" }),
  );
  if (response.status === 404 && notFoundValue !== undefined)
    return notFoundValue;
  if (!response.ok)
    throw new Error(
      response.status === 404
        ? "项目或任务不存在，或你没有访问权限。"
        : "读取失败，请重试。",
    );
  return response.json() as Promise<T>;
}

/** Filtering happens in the repository before pagination, not on the global recent list. */
export function loadProjectTasks(
  projectId: string,
  archived = false,
  offset = 0,
  limit = 50,
) {
  const query = new URLSearchParams({
    project_id: projectId,
    archived: String(archived),
    offset: String(offset),
    limit: String(limit),
  });
  return read<TaskSummary[]>(`/api/agui/threads?${query}`);
}
export async function loadTaskSummary(
  threadId: string,
): Promise<TaskSummary | null> {
  const tasks = await read<TaskSummary[]>(
    `/api/agui/threads?${new URLSearchParams({ thread_id: threadId })}`,
    [], // A new local draft has no durable thread summary yet.
  );
  return tasks[0] ?? null;
}
export function loadProjectArtifacts(projectId: string) {
  return read<ProjectArtifact[]>(
    `/api/harness/artifacts?${new URLSearchParams({ project_id: projectId, limit: "500" })}`,
  );
}

/** An explicit user action copies an authorized output into project inputs. */
export async function addResultToProject(
  projectId: string,
  artifact: ProjectArtifact,
) {
  const content = requireAuthenticatedResponse(
    await fetch(
      `/api/harness/artifacts/${encodeURIComponent(artifact.artifact_id)}`,
    ),
  );
  if (!content.ok) throw new Error("无法读取这份成果，请重试。");
  const form = new FormData();
  form.append(
    "file",
    new File([await content.blob()], artifact.name, {
      type: artifact.media_type,
    }),
  );
  const uploaded = requireAuthenticatedResponse(
    await fetch("/api/input-artifacts", { method: "POST", body: form }),
  );
  const body = await uploaded.json();
  if (!uploaded.ok || !body.input_artifact_id)
    throw new Error(
      body?.error?.message ??
        body?.detail?.message ??
        "加入项目资料失败，请检查文件大小与资料数量。",
    );
  await projectClient.attachDocument(projectId, body.input_artifact_id);
}
