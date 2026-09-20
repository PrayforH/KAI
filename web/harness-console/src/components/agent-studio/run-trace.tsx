"use client";

import { useEffect, useState } from "react";
import type { StudioTryRun } from "../../lib/studio-client";
import { requireAuthenticatedResponse } from "../../lib/client-auth";
import styles from "./run-trace.module.css";

type Event = StudioTryRun["events"][number];
export function RunTrace({ events, runId }: { events?: Event[]; runId?: string }) {
  const [loaded, setLoaded] = useState<Event[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [revision, refresh] = useState(0);
  const [filter, setFilter] = useState("all");
  const [selected, select] = useState<number>();
  useEffect(() => {
    if (events || !runId) return;
    const controller = new AbortController();
    setLoaded([]); setError(""); setLoading(true); select(undefined);
    void (async () => {
      try {
        const response = requireAuthenticatedResponse(await fetch(`/api/harness/runs/${encodeURIComponent(runId)}/events`, { cache: "no-store", signal: controller.signal }));
        if (!response.ok) throw new Error(response.status === 403 || response.status === 404 ? "无权读取或运行记录不存在" : `事件读取失败（${response.status}）`);
        const body = await response.text();
        const items = body.split("\n").filter(line => line.startsWith("data:")).map(line => JSON.parse(line.slice(5)) as Event);
        if (!controller.signal.aborted) setLoaded(items);
      } catch (reason) {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "事件读取失败");
      } finally { if (!controller.signal.aborted) setLoading(false); }
    })();
    return () => controller.abort();
  }, [events, runId, revision]);
  const all = events ?? loaded;
  const visible = all.filter(event => filter === "all" || (filter === "error" ? /error|fail|reject|timeout/.test(event.type) : event.type.startsWith(filter)));
  const active = visible.find(event => event.sequence === selected) ?? visible[0];
  const start = all[0] ? Date.parse(all[0].timestamp) : 0;
  return <section className={styles.trace} aria-label="运行事件 Trace">
    <header><strong>运行事件</strong><span>{all.length} 条 · {all.filter(e => e.type === "tool.request").length} 次工具请求</span>{runId && !events && <button onClick={() => refresh(value => value + 1)} disabled={loading}>刷新</button>}</header>
    <div className={styles.filters} role="group" aria-label="事件筛选">{[["all", "全部"], ["model", "模型"], ["tool", "工具"], ["approval", "审批"], ["error", "异常"]].map(([id, label]) => <button key={id} aria-pressed={filter === id} onClick={() => setFilter(id)}>{label}</button>)}</div>
    {error ? <p role="alert">{error}</p> : loading ? <p role="status">读取运行事件…</p> : !visible.length ? <p>当前没有匹配事件。未采集的数据不代表没有执行。</p> : <div className={styles.split}>
      <ol>{visible.map(event => <li key={event.event_id || event.sequence}><button aria-pressed={active?.sequence === event.sequence} onClick={() => select(event.sequence)}><time>{Math.max(0, (Date.parse(event.timestamp) - start) / 1000).toFixed(2)}s</time><span>{event.type}</span><small>#{event.sequence}</small></button></li>)}</ol>
      {active && <article><strong>{active.type}</strong><p>{new Date(active.timestamp).toLocaleString()}</p><pre>{JSON.stringify(active.payload, null, 2)}</pre></article>}
    </div>}
  </section>;
}
