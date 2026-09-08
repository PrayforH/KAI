import {
  agentIdentity,
  agentItemKey,
  type TaskAgent,
} from "./task-agent-catalog";
import type { TaskSummary } from "./task-history";

export type ProductivityActionId =
  | "new-task"
  | "studio-agents";

export type ProductivityCommandResult =
  | {
      kind: "action";
      id: ProductivityActionId;
      title: string;
      description: string;
      shortcut?: string;
    }
  | {
      kind: "task";
      id: string;
      title: string;
      description: string;
      task: TaskSummary;
    }
  | {
      kind: "agent";
      id: string;
      title: string;
      description: string;
      agent: TaskAgent;
    };

const actionCommands: ReadonlyArray<
  Extract<ProductivityCommandResult, { kind: "action" }> & { keywords: string }
> = [
  {
    kind: "action",
    id: "new-task",
    title: "新建任务",
    description: "保留当前智能体，打开一个空白任务",
    keywords: "new 新建 创建 空白 task 任务",
  },
  {
    kind: "action",
    id: "studio-agents",
    title: "创建或调整智能体",
    description: "进入构建区定义工作、能力和版本",
    keywords: "agent studio 智能体 助手 创建 编辑 发布",
  },
];

const taskStatusLabels: Record<string, string> = {
  idle: "新任务",
  queued: "排队中",
  running: "运行中",
  waiting_approval: "待审批",
  cancelling: "取消中",
  cancelled: "已取消",
  succeeded: "已完成",
  failed: "失败",
  rejected: "已拒绝",
  timed_out: "已超时",
};

function normalized(value: string) {
  return value.trim().toLocaleLowerCase();
}

function matchRank(title: string, searchText: string, query: string) {
  if (!query) return 0;
  const normalizedTitle = normalized(title);
  if (normalizedTitle === query) return 0;
  if (normalizedTitle.startsWith(query)) return 1;
  if (normalized(searchText).includes(query)) return 2;
  return Number.POSITIVE_INFINITY;
}

function ranked<T>(
  values: readonly T[],
  query: string,
  fields: (value: T) => { title: string; searchText: string },
) {
  return values
    .map((value, index) => ({
      value,
      index,
      rank: matchRank(fields(value).title, fields(value).searchText, query),
    }))
    .filter((item) => Number.isFinite(item.rank))
    .sort((left, right) => left.rank - right.rank || left.index - right.index)
    .map((item) => item.value);
}

/** One searchable entry per Agent identity. Published history stays available
 * in the version switcher, while global search follows the control plane's
 * current-version pointer (falling back to the newest semantic version). */
export function searchableTaskAgents(
  agents: readonly TaskAgent[],
): TaskAgent[] {
  const preferred = new Map<string, TaskAgent>();
  for (const agent of agents) {
    const identity = agentIdentity(agent);
    const current = preferred.get(identity);
    if (!current) {
      preferred.set(identity, agent);
      continue;
    }
    const agentIsCurrent = agent.version === agent.currentVersion;
    const currentIsCurrent = current.version === current.currentVersion;
    if (
      (agentIsCurrent && !currentIsCurrent) ||
      (agentIsCurrent === currentIsCurrent &&
        agent.version.localeCompare(current.version, undefined, { numeric: true }) > 0)
    ) {
      preferred.set(identity, agent);
    }
  }
  return [...preferred.values()];
}

export function productivityCommandResults(
  query: string,
  tasks: readonly TaskSummary[],
  agents: readonly TaskAgent[],
): ProductivityCommandResult[] {
  const needle = normalized(query);
  const actions = ranked(actionCommands, needle, (action) => ({
    title: action.title,
    searchText: `${action.title} ${action.description} ${action.keywords}`,
  })).map(({ keywords: _keywords, ...action }) => action);
  const taskResults = ranked(
    [...tasks].sort(
      (left, right) =>
        new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime(),
    ),
    needle,
    (task) => ({
      title: task.title,
      searchText: `${task.title} ${task.agent_name} ${task.agent_version} ${task.status} ${taskStatusLabels[task.status] ?? ""}`,
    }),
  )
    .slice(0, 6)
    .map((task) => ({
      kind: "task" as const,
      id: task.thread_id,
      title: task.title,
      description: `${taskStatusLabels[task.status] ?? task.status} · ${task.agent_name}@${task.agent_version}`,
      task,
    }));
  const agentResults = ranked(searchableTaskAgents(agents), needle, (agent) => ({
    title: agent.displayName,
    searchText: `${agent.displayName} ${agent.name} ${agent.version} ${agent.domain} ${agent.spaceName ?? ""} ${agent.scope === "team" ? "团队 协作 空间" : "个人"}`,
  }))
    .map((agent) => ({
      kind: "agent" as const,
      id: agentItemKey(agent),
      title: agent.displayName,
      description: `${agent.spaceName ?? (agent.scope === "team" ? "团队智能体" : "个人智能体")} · 当前版本 ${agent.version}`,
      agent,
    }));
  return [...actions, ...taskResults, ...agentResults];
}

export function productivityCommandKey(result: ProductivityCommandResult) {
  return `${result.kind}:${result.id}`;
}
