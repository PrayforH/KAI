import { StructuredValue } from "./structured-value";
import { KnowledgeCitations } from "./knowledge/knowledge-citations";
import { useRunViewModel } from "../lib/activity-store";
import type { RunToolNode, RunViewModel } from "../lib/run-view-model";
import {
  toolActivitySentence,
  toolBatchTitle,
  toolTitle,
} from "../lib/tool-presentation";

type ToolStatus = "inProgress" | "executing" | "complete";

const statusLabel: Record<ToolStatus, string> = {
  inProgress: "准备中",
  executing: "执行中",
  complete: "已完成",
};

export { toolTitle } from "../lib/tool-presentation";

const standaloneToolNames = new Set([
  "Task",
  "Agent",
  "harness_request_approval",
  "harness_present_artifact",
]);

export function completedToolBatch(view: RunViewModel | undefined) {
  return view?.tools.filter(
    (tool) => tool.status === "completed" && !standaloneToolNames.has(tool.name),
  ) ?? [];
}

function batchDigest(tools: readonly RunToolNode[]) {
  const counts = new Map<string, number>();
  for (const tool of tools) {
    const title = toolTitle(tool.name);
    counts.set(title, (counts.get(title) ?? 0) + 1);
  }
  return [...counts]
    .map(([title, count]) => count > 1 ? `${title} ×${count}` : title)
    .join(" · ");
}

function CompletedToolBatch({ tools }: { tools: readonly RunToolNode[] }) {
  return (
    <details className="tool-card tool-status-complete tool-card-batch">
      <summary>
        <span className="tool-glyph tool-batch-glyph" aria-hidden="true"><i /></span>
        <span className="tool-card-title">
          <strong>{toolBatchTitle(tools)}</strong>
          <small>{tools.length} 项 · {batchDigest(tools)}</small>
        </span>
        <span className="tool-status-label">已收起</span>
        <span className="tool-chevron" aria-hidden="true" />
      </summary>
      <div className="tool-batch-list">
        {tools.map((tool) => (
          <div className="tool-batch-row" key={tool.id}>
            <span className="tool-batch-copy">
              <span>
                <strong>{toolActivitySentence(tool)}</strong>
              </span>
              <span className="tool-batch-facts">
                {tool.resultSummary && <em>{tool.resultSummary}</em>}
              </span>
            </span>
            <small>已完成</small>
          </div>
        ))}
      </div>
    </details>
  );
}

export function ToolCard({
  toolCallId,
  name,
  status,
  args,
  result,
  isError = false,
}: {
  toolCallId?: string;
  name: string;
  status: ToolStatus;
  args: unknown;
  result?: unknown;
  isError?: boolean;
}) {
  const view = useRunViewModel();
  const batch = completedToolBatch(view);
  const batchIndex = toolCallId
    ? batch.findIndex((tool) => tool.id === toolCallId)
    : -1;
  const citations = toolCallId
    ? view?.tools.find((tool) => tool.id === toolCallId)?.citations
    : undefined;

  if (!isError && status === "complete" && batch.length > 1 && batchIndex >= 0) {
    return batchIndex === 0 ? <CompletedToolBatch tools={batch} /> : null;
  }

  return (
    <details className={`tool-card tool-status-${status}`} open={status !== "complete"}>
      <summary>
        <span className="tool-glyph" aria-hidden="true"><i /></span>
        <span className="tool-card-title">
          <strong>{toolTitle(name)}</strong>
          <small>{name}</small>
        </span>
        <span className="tool-status-label">{statusLabel[status]}</span>
        <span className="tool-chevron" aria-hidden="true" />
      </summary>
      <div className="tool-card-body">
        <StructuredValue value={args} label="输入" />
        {result !== undefined && <StructuredValue value={result} label="输出" />}
        {citations && citations.length > 0 ? (
          <KnowledgeCitations citations={citations} />
        ) : null}
      </div>
    </details>
  );
}
