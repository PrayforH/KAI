"use client";

import { useEffect, useState } from "react";
import {
  loadCompactionDetail, loadThreadCompactions,
  type CompactionDetail, type CompactionObservation, type HistoryMessageView,
} from "../lib/context-client";

function MessageList({ messages }: { messages: HistoryMessageView[] }) {
  return <div className="context-history-messages">{messages.map((message, index) => (
    <article key={index}>
      <small>{message.role === "assistant" ? "助手" : "用户 / 历史摘要"} · {index + 1}</small>
      <pre>{message.content}</pre>
      {message.truncated && <p>内容较长，此处仅展示前段；完整原文请在对话记录中查看。</p>}
    </article>
  ))}</div>;
}

export function CompactionContent({ detail }: { detail: CompactionDetail }) {
  if (detail.status !== "available" || !detail.after || !detail.summary) {
    return <p className="context-history-note">{detail.reason || "暂无可展示的摘要正文。"}</p>;
  }
  return <div className="context-compaction-content">
    <div className="context-history-metrics">
      <div><small>上一轮历史</small><strong>{detail.before ? detail.before.characters.toLocaleString() : "—"}<span> 字符</span></strong></div>
      <span aria-hidden="true">→</span>
      <div><small>本轮结束历史</small><strong>{detail.after.characters.toLocaleString()}<span> 字符</span></strong></div>
    </div>
    <p className="context-history-note">对照会话历史检查点，不含系统提示词和工具定义。右侧计数包含本轮新消息，不能直接视为 token 压缩率。</p>
    {detail.compaction_count > 1 && <p className="context-history-note">本轮发生 {detail.compaction_count} 次压缩，下面展示最后一次摘要及本轮结束时保存的历史。</p>}
    <section className="context-compaction-summary">
      <h4>压缩后摘要</h4>
      <pre>{detail.summary.content.match(/<summary>\s*([\s\S]*?)\s*<\/summary>\s*$/)?.[1] ?? detail.summary.content}</pre>
      {detail.summary.truncated && <p>摘要较长，当前展示已截断。</p>}
    </section>
    <details className="context-history-section">
      <summary>压缩前历史参考{detail.before ? ` · ${detail.before.message_count} 条消息` : ""}</summary>
      <p className="context-history-note">上一轮保存的有效历史，可能含更早的摘要。不包含本轮新增内容或工具交互，不等于摘要模型的完整输入。</p>
      {detail.before ? <MessageList messages={detail.before.messages} /> : <p>没有可读取的前序历史检查点，请回看原始对话。</p>}
      {detail.before?.truncated && <p className="context-history-note">历史较长，当前展示已截断。</p>}
    </details>
    <details className="context-history-section">
      <summary>保留的近期消息 · {Math.max(0, detail.after.message_count - 1)} 条</summary>
      <p className="context-history-note">除上方摘要外，继续对话会使用这些消息，包含本轮新提问及回答。</p>
      <MessageList messages={detail.after.messages.slice(1)} />
      {detail.after.truncated && <p className="context-history-note">历史较长，当前展示已截断。</p>}
    </details>
  </div>;
}

function CompactionRecord({ threadId, item }: { threadId: string; item: CompactionObservation }) {
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<CompactionDetail | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!open || detail) return;
    let alive = true;
    setError("");
    loadCompactionDetail(threadId, item.source_run_id).then(
      (value) => { if (alive) setDetail(value); },
      (cause) => { if (alive) setError(cause instanceof Error ? cause.message : "加载失败"); },
    );
    return () => { alive = false; };
  }, [open, detail, threadId, item.source_run_id, attempt]);
  return <details className="context-compaction-record" onToggle={(event) => setOpen(event.currentTarget.open)}>
    <summary>
      <span><strong>自动压缩</strong><small>{new Date(item.completed_at).toLocaleString("zh-CN")} · {item.runtime}</small></span>
      <span>{open ? "收起" : "查看前后内容"}</span>
    </summary>
    {open && <div>
      {error ? <p role="alert">{error} <button type="button" onClick={() => setAttempt(attempt + 1)}>重试</button></p>
        : detail ? <CompactionContent detail={detail} /> : <p role="status">正在读取已有历史记录…</p>}
    </div>}
  </details>;
}

export function ContextCompactionHistory({ threadId }: { threadId: string }) {
  const [data, setData] = useState<{ items: CompactionObservation[]; has_older: boolean } | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let alive = true;
    setError("");
    loadThreadCompactions(threadId).then(
      (value) => { if (alive) setData(value); },
      (cause) => { if (alive) setError(cause instanceof Error ? cause.message : "加载失败"); },
    );
    return () => { alive = false; };
  }, [threadId, attempt]);
  return <section className="context-compaction-history" aria-label="自动压缩记录">
    <h3>压缩记录</h3>
    <p className="context-history-note">摘要替代较早的历史，近期消息继续保留。原始对话可随时回看，无需手动恢复即可继续提问。</p>
    {error ? <p role="alert">{error} <button type="button" onClick={() => setAttempt(attempt + 1)}>重试</button></p>
      : !data ? <p role="status">正在加载压缩记录…</p>
        : data.items.length === 0 ? <p className="context-history-note">尚未触发自动压缩。</p>
          : data.items.map((item) => <CompactionRecord key={item.source_run_id} threadId={threadId} item={item} />)}
    {data?.has_older && <p className="context-history-note">当前展示最近 20 次压缩涉及的轮次，更早记录保留在运行日志中。</p>}
  </section>;
}
