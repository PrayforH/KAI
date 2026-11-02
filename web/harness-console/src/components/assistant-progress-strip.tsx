"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import Link from "next/link";
import { assistantRequest, type Notice } from "../lib/assistant-client";
import { CheckpointMeteringLine } from "./checkpoint-metering";

const KIND_LABEL: Record<string, { label: string; tone: "done" | "working" | "attention" }> = {
  succeeded: { label: "本次执行完成", tone: "done" },
  artifact_ready: { label: "成果已就绪", tone: "done" },
  failed: { label: "需要你处理", tone: "attention" },
  timed_out: { label: "需要你处理", tone: "attention" },
  rejected: { label: "需要你处理", tone: "attention" },
  cancelled: { label: "已停止", tone: "attention" },
  pending: { label: "正在推进", tone: "working" },
  running: { label: "正在推进", tone: "working" },
};

const FAILURE_KINDS = new Set(["failed", "timed_out", "rejected"]);

/**
 * 进展条目要打开哪里：失败 → 原线程的失败回放（trace=1，有则带 run 与
 * `checkpoint_<executionId>`，与写库口径一致）；其余 → 仅原线程。
 * 旧 notice 没有 runId/executionId 时不伪造参数，失败仍可进轨迹页。
 */
export function progressNoticeLink(
  notice: Pick<Notice, "kind" | "threadId" | "runId" | "executionId">,
): { href: string; label: string; mode: "failure" | "thread" } | null {
  if (!notice.threadId) return null;
  const failure = FAILURE_KINDS.has(notice.kind);
  const botThread = notice.threadId.startsWith("assistant_specialist_");
  const params = new URLSearchParams(botThread
    ? { bot: notice.threadId.slice("assistant_".length) }
    : { thread: notice.threadId });
  if (notice.runId) params.set("run", notice.runId);
  if (failure) {
    params.set("trace", "1");
    if (notice.runId) params.set("run", notice.runId);
    if (notice.executionId) params.set("checkpoint", `checkpoint_${notice.executionId}`);
  }
  return {
    href: `${botThread ? "/assistant" : "/"}?${params.toString()}`,
    label: failure ? "打开失败回放 →" : "打开原任务 →",
    mode: failure ? "failure" : "thread",
  };
}

/**
 * 8.5 结果优先：主助理页只突出需要处理和真实执行/成果状态。
 * 执行记录与完整过程仍在原任务；这里的卡片是引用，可追溯到原会话。
 */
export function AssistantProgressStrip() {
  const [notices, setNotices] = useState<Notice[]>([]);
  const [reading, setReading] = useState<ReadonlySet<string>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const receiptVersion = useRef(0);
  // Header entry only; background results never cover the conversation.
  const [expanded, setExpanded] = useState(false);
  const panel = useRef<HTMLElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  const listId = useId();
  useEffect(() => {
    if (!expanded) return;
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !panel.current?.contains(event.target)) setExpanded(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setExpanded(false); toggle.current?.focus(); }
    };
    document.addEventListener("pointerdown", outside);
    document.addEventListener("keydown", escape);
    return () => {
      document.removeEventListener("pointerdown", outside);
      document.removeEventListener("keydown", escape);
    };
  }, [expanded]);

  const load = useCallback(async () => {
    const version = receiptVersion.current;
    try {
      const list = await assistantRequest<Notice[]>("inbox");
      if (version === receiptVersion.current) {
        setNotices(list.filter((item) => !item.readAt).slice(0, 6));
      }
    } catch {
      // 后台进展是增强信息；读取失败不打断主对话。
    }
  }, []);

  const markRead = async (item: Notice) => {
    setReading((current) => new Set(current).add(item.itemId));
    setError(null);
    try {
      await assistantRequest(`inbox/${encodeURIComponent(item.itemId)}/read`, "POST", {
        expectedSourceSequence: item.sourceSequence ?? null,
      });
      receiptVersion.current += 1;
      setNotices((current) => current.filter((notice) =>
        notice.itemId !== item.itemId || notice.sourceSequence !== item.sourceSequence));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "未能标为已读，请稍后重试。");
      void load();
    } finally {
      setReading((current) => {
        const next = new Set(current);
        next.delete(item.itemId);
        return next;
      });
    }
  };

  useEffect(() => {
    void load();
    const timer = setInterval(() => {
      if (!document.hidden) void load();
    }, 15000);
    return () => clearInterval(timer);
  }, [load]);

  const visible = notices;
  if (!visible.length) return null;
  const attention = visible.filter((item) => KIND_LABEL[item.kind]?.tone === "attention").length;
  const summary = [
    visible.length ? `${visible.length} 项进展` : "",
    attention ? `${attention} 项需要你处理` : "",
  ]
    .filter(Boolean)
    .join(" · ");
  const shown = expanded ? visible : visible.slice(0, 3);

  return (
    <aside
      ref={panel}
      className="assistant-progress-strip"
      data-expanded={expanded ? "true" : "false"}
      aria-label="新进展"
    >
      <div className="assistant-progress-head">
        <button
          type="button"
          ref={toggle}
          className="assistant-progress-toggle"
          aria-controls={expanded ? listId : undefined}
          aria-expanded={expanded}
          aria-label={`查看新进展：${summary}`}
          onClick={() => setExpanded((value) => !value)}
        >
          <span className="assistant-progress-dot" data-tone={attention ? "attention" : "done"} />
          <span className="assistant-progress-summary">{visible.length} 条新进展{attention ? ` · ${attention} 待处理` : ""}</span>
          <span className="assistant-progress-chevron" aria-hidden="true">{expanded ? "⌃" : "⌄"}</span>
        </button>
        <button
          type="button"
          className="assistant-progress-hide"
          aria-label="关闭新进展列表"
          title="收起"
          hidden={!expanded}
          onClick={() => setExpanded(false)}
        >
          ×
        </button>
      </div>
      {expanded && (
      <ul id={listId}>
        {error && <li data-tone="attention"><p role="alert">{error}</p></li>}
        {shown.map((item) => {
          const tone = KIND_LABEL[item.kind]?.tone ?? "working";
          const link = progressNoticeLink(item);
          return (
            <li key={item.itemId} data-tone={tone}>
              <span className="assistant-progress-kind">{KIND_LABEL[item.kind]?.label ?? item.kind}</span>
              <span className="assistant-progress-title">{item.title}</span>
              <span className="assistant-progress-excerpt">{item.content}</span>
              <CheckpointMeteringLine
                metrics={item.metrics}
                durationSeconds={item.durationSeconds}
                className="assistant-progress-metrics"
              />
              {link && (
                <Link
                  className="assistant-progress-link"
                  data-replay={link.mode}
                  href={link.href}
                >
                  {link.label}
                </Link>
              )}
              <button
                type="button"
                aria-label={`将${item.title}的这条进展标为已读`}
                title="标为已读"
                disabled={reading.has(item.itemId)}
                onClick={() => void markRead(item)}
              >
                {reading.has(item.itemId) ? "保存中" : "已读"}
              </button>
            </li>
          );
        })}
      </ul>
      )}
    </aside>
  );
}
