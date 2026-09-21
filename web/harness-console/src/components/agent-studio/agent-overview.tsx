"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import {
  studioClient,
  type StudioDraftSummary,
  type StudioTryRunSummary,
} from "../../lib/studio-client";
import styles from "./agenta-workspace.module.css";
export function AgentOverview({
  draft,
  currentVersion,
  actions,
  qualityCount,
  datasetCount,
  experienceCount,
}: {
  draft: StudioDraftSummary;
  currentVersion: string | null | undefined;
  actions: { label: string; count: number; section: string }[];
  qualityCount: number | null;
  datasetCount: number | null;
  experienceCount: number | null;
}) {
  const [history, setHistory] = useState<StudioTryRunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  useEffect(() => {
    let current = true;
    setLoading(true);
    setError("");
    void studioClient
      .listTryRuns(draft.draftId)
      .then((items) => {
        if (current) setHistory(items);
      })
      .catch((reason) => {
        if (current)
          setError(reason instanceof Error ? reason.message : "会话加载失败");
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [draft.draftId]);
  const href = (section: string, session?: string) =>
    `/studio/agents/${encodeURIComponent(draft.name)}?${new URLSearchParams({ draft: draft.draftId, section, ...(session ? { session } : {}) })}`;
  const sessions = [
    ...new Map(
      [...history].reverse().map((item) => [item.run.session_id, item]),
    ).values(),
  ]
    .reverse()
    .slice(0, 6);
  const statuses: Record<string, string> = {
    queued: "排队中",
    provisioning: "准备中",
    running: "运行中",
    waiting_approval: "等待审批",
    succeeded: "已完成",
    failed: "失败",
    cancelled: "已取消",
    cancelling: "取消中",
    timed_out: "超时",
    rejected: "已拒绝",
  };
  return (
    <div className={styles.agentOverview}>
      <div className={styles.activityColumn}>
        <section className={styles.startConversation}>
          <span className={styles.agentIcon}>◇</span>
          <h2>今天让 {draft.displayName} 做什么？</h2>
          <p>{draft.goal || "从一个真实任务开始，检查回答并持续改进。"}</p>
          <Link href={href("sessions")}>
            开始新会话 <span>↗</span>
          </Link>
          <small>在 Playground 中使用当前草稿试运行</small>
        </section>
        {actions.some((item) => item.count > 0) && (
          <section className={styles.attention}>
            <h3>需要你处理</h3>
            {actions
              .filter((item) => item.count > 0)
              .map((item) => (
                <Link key={item.label} href={href(item.section)}>
                  <span>{item.label}</span>
                  <strong>{item.count}</strong>
                  <span>→</span>
                </Link>
              ))}
          </section>
        )}
        <section className={styles.recentSessions}>
          <header>
            <h3>最近的试运行会话</h3>
            <Link href={href("sessions")}>查看全部 →</Link>
          </header>
          {loading ? (
            <p role="status">正在读取会话…</p>
          ) : error ? (
            <p role="alert">{error}</p>
          ) : sessions.length ? (
            sessions.map((item) => (
              <Link
                key={item.run.session_id}
                href={href("sessions", item.run.session_id)}
              >
                <div>
                  <strong>{item.run.input.prompt || "历史任务"}</strong>
                  <small>
                    草稿 r{item.draftRevision} ·{" "}
                    {new Date(item.run.created_at).toLocaleString()}
                  </small>
                </div>
                <span data-status={item.run.status}>
                  {statuses[item.run.status] || item.run.status}
                </span>
              </Link>
            ))
          ) : (
            <div className={styles.activityEmpty}>
              <strong>等待第一段会话</strong>
              <p>
                完成一次试运行后，可以在这里返回上下文，继续提问或检查结果。
              </p>
              <Link href={href("playground")}>打开 Playground →</Link>
            </div>
          )}
        </section>
      </div>
      <aside className={styles.overviewRail}>
        <section>
          <header>
            <h3>Configuration</h3>
            <Link href={href("playground")}>编辑 ↗</Link>
          </header>
          <dl>
            <dt>当前发布</dt>
            <dd>
              {currentVersion || (draft.spaceId ? "团队管理" : "尚未发布")}
            </dd>
            <dt>草稿</dt>
            <dd>
              r{draft.revision} · v{draft.version}
            </dd>
            <dt>Tools</dt>
            <dd>{draft.toolCount ?? 0} 个工具</dd>
            <dt>Skills</dt>
            <dd>{draft.skillCount ?? 0} 个技能</dd>
            <dt>目标产物</dt>
            <dd>{draft.primaryOutput || "尚未填写"}</dd>
          </dl>
        </section>
        <section>
          <h3>Triggers</h3>
          <Link href={href("automation")}>
            Subscriptions <span>→</span>
          </Link>
          <Link href={href("automation")}>
            Schedules <span>→</span>
          </Link>
        </section>
        <section>
          <h3>质量与改进</h3>
          <dl>
            <dt>有质量记录的运行</dt>
            <dd>{qualityCount ?? "不可用"}</dd>
            <dt>评测集版本</dt>
            <dd>{datasetCount ?? "不可用"}</dd>
            <dt>已审核经验</dt>
            <dd>{experienceCount ?? "不可用"}</dd>
          </dl>
          <p>统计只覆盖已采集记录。</p>
          <Link href={href("diagnostics")}>查看运行与诊断 →</Link>
        </section>
      </aside>
    </div>
  );
}
