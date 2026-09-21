import { requireAuthenticatedResponse } from "./client-auth";
import type { TaskAgent } from "./task-agent-catalog";
import { notifyTaskListChanged, type TaskSummary } from "./task-history";

/** Create the durable project binding before opening the empty composer. */
export async function createProjectTask(projectId: string, agent: TaskAgent | null): Promise<TaskSummary> {
  if (!agent) throw new Error("智能体尚未加载，请稍后重试");
  const response = requireAuthenticatedResponse(await fetch("/api/agui/threads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      project_id: projectId,
      agent_name: agent.name,
      agent_version: agent.version,
      agent_owner_user_id: agent.ownerUserId,
      space_id: agent.spaceId,
    }),
  }));
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(error?.error?.message ?? "新建任务失败，请稍后重试");
  }
  const task = await response.json() as TaskSummary;
  notifyTaskListChanged();
  return task;
}
