"use client";

import { useRef } from "react";
import { createPortal } from "react-dom";
import {
  activityOverview,
  type ActivityItem,
  type RunActivity,
} from "../lib/activity-schema";
import { useDialogFocus } from "../lib/use-dialog-focus";

const statusLabels: Record<string, string> = {
  queued: "排队中",
  running: "运行中",
  waiting: "等待中",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已停止",
  rejected: "已拒绝",
  timed_out: "已超时",
};

const noisyEventTypes = new Set([
  "message.delta",
  "subagent.delta",
  "subagent.progress",
  "subagent.updated",
  "runtime.system",
  "message.start",
  "message.completed",
]);

interface TraceEntry {
  id: string;
  kind: ActivityItem["kind"];
  status: string;
  title: string;
  summary?: string;
  sequence: number;
  timestamp: string;
  durationMs?: number;
  input?: string;
  output?: string;
  artifact?: {
    id: string;
    name: string;
    mediaType?: string;
    sizeBytes?: number;
  };
}

function traceDuration(start: string, end: string) {
  const duration = Date.parse(end) - Date.parse(start);
  return Number.isFinite(duration) && duration >= 0 ? duration : undefined;
}

function traceToolTitle(name: string, argumentsValue: Record<string, unknown>) {
  const description = argumentsValue.description;
  if (typeof description === "string" && description.trim()) return description.trim();
  const labels: Record<string, string> = {
    Bash: "运行命令",
    Glob: "查找文件",
    Grep: "搜索内容",
    Read: "读取文件",
    Write: "写入文件",
    Edit: "编辑文件",
    Task: "委派子任务",
    Agent: "委派子任务",
  };
  return labels[name] ?? `调用 ${name}`;
}

function traceToolInput(name: string, argumentsValue: Record<string, unknown>) {
  if (name === "Bash" && typeof argumentsValue.command === "string") {
    return argumentsValue.command;
  }
  return JSON.stringify(argumentsValue, null, 2);
}

/** Build a compact audit trace while keeping each tool input and result inspectable. */
export function traceActivityEntries(items: readonly ActivityItem[]): TraceEntry[] {
  const results = new Map<string, ActivityItem>();
  const approvals = new Map<string, ActivityItem>();
  const messageGroups = new Map<string, TraceEntry>();

  for (const item of items) {
    const toolCallId = item.metadata.tool_call_id;
    if (typeof toolCallId !== "string") continue;
    if (item.event_type === "tool.result" || item.event_type === "tool.allowed") {
      results.set(toolCallId, item);
    } else if (item.event_type === "approval.requested") {
      approvals.set(toolCallId, item);
    }
  }

  const entries: TraceEntry[] = [];
  for (const item of items) {
    if (item.event_type === "message.delta") {
      if (!item.summary?.trim()) continue;
      const rawMessageId = item.metadata.message_id;
      const messageId = typeof rawMessageId === "string" ? rawMessageId : "model-progress";
      const existing = messageGroups.get(messageId);
      if (existing) {
        existing.output = `${existing.output ?? ""}${item.summary}`;
        existing.durationMs = traceDuration(existing.timestamp, item.timestamp);
      } else {
        const entry: TraceEntry = {
          id: `message-${messageId}`,
          kind: "analysis",
          status: "succeeded",
          title: "模型进展说明",
          sequence: item.sequence,
          timestamp: item.timestamp,
          output: item.summary,
        };
        messageGroups.set(messageId, entry);
        entries.push(entry);
      }
      continue;
    }

    if (noisyEventTypes.has(item.event_type)) continue;
    if (
      item.event_type === "tool.result" ||
      item.event_type === "tool.allowed" ||
      item.event_type === "approval.requested"
    ) continue;

    if (item.event_type === "tool.request") {
      const name = typeof item.metadata.name === "string" ? item.metadata.name : "工具";
      const rawArguments = item.metadata.arguments;
      const argumentsValue =
        rawArguments && typeof rawArguments === "object" && !Array.isArray(rawArguments)
          ? rawArguments as Record<string, unknown>
          : {};
      const toolCallId =
        typeof item.metadata.tool_call_id === "string" ? item.metadata.tool_call_id : item.id;
      const result = results.get(toolCallId);
      const approval = approvals.get(toolCallId);
      const resultSummary = result?.metadata.result_summary;
      const resultPreview = result?.metadata.result_preview;
      entries.push({
        id: `trace-${toolCallId}`,
        kind: item.kind,
        status: result?.status ?? approval?.status ?? item.status,
        title: traceToolTitle(name, argumentsValue),
        summary:
          typeof resultSummary === "string"
            ? resultSummary
            : approval?.summary ?? item.summary ?? undefined,
        sequence: item.sequence,
        timestamp: item.timestamp,
        durationMs: result ? traceDuration(item.timestamp, result.timestamp) : undefined,
        input: traceToolInput(name, argumentsValue),
        output: typeof resultPreview === "string" ? resultPreview : undefined,
      });
      continue;
    }

    if (item.event_type === "artifact.ready") {
      const artifactId = item.metadata.artifact_id;
      if (typeof artifactId === "string") {
        const sourcePath =
          typeof item.metadata.source_path === "string"
            ? item.metadata.source_path
            : undefined;
        entries.push({
          id: item.id,
          kind: item.kind,
          status: item.status,
          title: "生成运行产物",
          summary: sourcePath ?? item.summary ?? undefined,
          sequence: item.sequence,
          timestamp: item.timestamp,
          artifact: {
            id: artifactId,
            name: sourcePath ?? item.summary ?? "未命名产物",
            mediaType:
              typeof item.metadata.media_type === "string"
                ? item.metadata.media_type
                : undefined,
            sizeBytes:
              typeof item.metadata.size_bytes === "number"
                ? item.metadata.size_bytes
                : undefined,
          },
        });
        continue;
      }
    }

    entries.push({
      id: item.id,
      kind: item.kind,
      status: item.status,
      title: item.title,
      summary: item.summary ?? undefined,
      sequence: item.sequence,
      timestamp: item.timestamp,
    });
  }
  return entries.sort((left, right) => left.sequence - right.sequence);
}

export function notableActivityItems(items: readonly ActivityItem[]) {
  return items
    .filter((item) => {
      if (noisyEventTypes.has(item.event_type)) return false;
      if (
        item.kind === "tool" &&
        ["succeeded", "completed", "running"].includes(item.status)
      ) return false;
      return true;
    })
    .slice(-12);
}

export function DeveloperDrawer({
  threadId,
  activity,
  onClose,
}: {
  threadId: string;
  activity: RunActivity;
  onClose?: () => void;
}) {
  const overview = activity ? activityOverview(activity) : undefined;
  const panelRef = useRef<HTMLElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  useDialogFocus({
    open: true,
    panelRef,
    initialFocusRef: closeButtonRef,
    onEscape: () => onClose?.(),
  });

  return createPortal(
    <div
      className="run-details-backdrop"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose?.();
      }}
    >
    <aside
      ref={panelRef}
      id="run-details-panel"
      className="developer-drawer"
      aria-label="运行详情"
      role="dialog"
      aria-modal="true"
    >
      <header className="inspector-header">
        <div className="inspector-title">
          <div>
            <p>本次运行</p>
            <h2>运行详情</h2>
          </div>
        </div>
        {onClose && (
          <button ref={closeButtonRef} type="button" className="inspector-close" onClick={onClose} aria-label="关闭运行详情"><span aria-hidden="true" /></button>
        )}
      </header>

      {activity && overview ? (
        <>
          <section className="run-overview">
            <div className="run-overview-status">
              <span className={`activity-pulse status-${activity.status}`} aria-hidden="true" />
              <div><strong>{statusLabels[activity.status] ?? activity.status}</strong><small>本次运行</small></div>
              <span className="run-overview-duration">{overview.duration}</span>
            </div>
            <dl className="run-metrics">
              <div className="run-metric-wide"><dt>模型</dt><dd title={overview.model}>{overview.model}</dd></div>
              <div><dt>服务</dt><dd>{overview.provider}</dd></div>
              <div><dt>轮次</dt><dd>{overview.turns}</dd></div>
              <div><dt>费用</dt><dd>{overview.cost}</dd></div>
              <div><dt>耗时</dt><dd>{overview.duration}</dd></div>
            </dl>
            <div className="run-counts">
              <span><strong>{overview.toolCalls}</strong> 工具</span>
              <span><strong>{overview.subagents}</strong> 子任务</span>
              <span>结束原因 <strong>{overview.stopReason === "—" ? "执行中" : overview.stopReason}</strong></span>
            </div>
          </section>

          <section className="observability-panel" aria-label="外部观测">
            <div className="inspector-section-title"><span>可观测性</span><span>Langfuse</span></div>
            {activity.trace_id ? (
              <a
                className="observability-link"
                href={`/api/harness/observability?run_id=${encodeURIComponent(activity.run_id)}&trace_id=${encodeURIComponent(activity.trace_id)}`}
                target="_blank"
                rel="noreferrer"
              >
                <span>
                  <strong>打开 Trace</strong>
                  <small title={activity.trace_id}>{activity.trace_id.slice(0, 12)}…</small>
                </span>
                <span className="external-arrow" aria-hidden="true">↗</span>
              </a>
            ) : (
              <div className="observability-link is-disabled">
                <span>
                  <strong>Trace 尚未生成</strong>
                  <small>运行开始后会在这里提供直接链接</small>
                </span>
              </div>
            )}
          </section>

          <details className="run-identifiers">
            <summary>运行标识</summary>
            <dl>
              <div><dt>Run</dt><dd><code>{activity.run_id}</code></dd></div>
              <div><dt>Thread</dt><dd><code>{threadId}</code></dd></div>
            </dl>
          </details>
        </>
      ) : (
        <div className="inspector-empty">
          <strong>还没有运行记录</strong>
          <p>提交任务后，可在这里查看运行摘要并跳转到外部 Trace。</p>
        </div>
      )}
    </aside>
    </div>,
    document.body,
  );
}
