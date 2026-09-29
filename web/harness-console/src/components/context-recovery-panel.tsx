"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { useRunViewModel } from "../lib/activity-store";
import {
  type ContextBudgetLevel,
  contextTrustLabels,
  loadThreadContext,
  mergeContextPages,
  rebaseThreadContext,
  rollbackThreadContextRebase,
  shortContextHash,
  type ContextDigestEntry,
  type ContextDigestObjectRef,
  type SessionContextDigest,
  type SessionContextOverview,
} from "../lib/context-client";
import { ContextCompactionHistory } from "./context-compaction-history";
import { useDialogFocus } from "../lib/use-dialog-focus";

const contextBudgetLabels: Record<ContextBudgetLevel, string> = {
  green: "窗口充足",
  watch: "接近软阈值",
  compact_ready: "接近压缩阈值",
  emergency: "需要立即处理",
};

const contextBudgetGuidance: Record<ContextBudgetLevel, string> = {
  green: "当前上下文空间充足，可继续对话。",
  watch: "可减少非必要的附件与上下文，自动压缩按智能体配置执行。",
  compact_ready: "自动压缩由运行时按配置触发，原始对话记录会保留。",
  emergency: "窗口余量较低，可减少新增材料；如仍超出上限，可另开任务。",
};

function formatTokens(value: number) {
  return new Intl.NumberFormat("zh-CN", { notation: "compact", maximumFractionDigits: 1 }).format(value);
}

function formatTimestamp(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function ContextEntries({ title, entries }: { title: string; entries: ContextDigestEntry[] }) {
  if (entries.length === 0) return null;
  return (
    <section className="context-recovery-group">
      <h4>{title}</h4>
      {entries.map((entry, index) => (
        <div className="context-recovery-entry" key={`${title}-${index}-${entry.text}`}>
          <p>{entry.text}</p>
          <small>
            {contextTrustLabels[entry.trust]} · {entry.source_refs.join(" · ")}
          </small>
        </div>
      ))}
    </section>
  );
}

function ContextObjects({ title, items }: { title: string; items: ContextDigestObjectRef[] }) {
  if (items.length === 0) return null;
  return (
    <section className="context-recovery-group">
      <h4>{title}</h4>
      {items.map((item) => (
        <div className="context-recovery-object" key={item.ref}>
          <span>{item.title}</span>
          <small>{item.ref} · {shortContextHash(item.content_hash)}</small>
        </div>
      ))}
    </section>
  );
}

function RecoveryPoint({ digest, latest }: { digest: SessionContextDigest; latest: boolean }) {
  return (
    <details className="context-recovery-point" open={latest}>
      <summary>
        <span className="context-recovery-version">v{digest.version}</span>
        <span>
          <strong>{latest ? "当前恢复点" : "历史恢复点"}</strong>
          <small>{formatTimestamp(digest.created_at)} · {contextTrustLabels[digest.trust_high_watermark]}</small>
        </span>
        <span className="context-recovery-chevron" aria-hidden="true" />
      </summary>
      <div className="context-recovery-detail">
        <ContextEntries title="已确认事实" entries={digest.facts} />
        <ContextEntries title="关键决定" entries={digest.decisions} />
        <ContextEntries title="未完成事项" entries={digest.open_tasks} />
        <ContextObjects title="任务产物" items={digest.artifact_refs} />
        <ContextObjects title="工作区快照" items={digest.workspace_refs} />
        <dl className="context-recovery-proof">
          <div><dt>截至运行</dt><dd>{digest.source.through_run_id}</dd></div>
          <div><dt>事件序号</dt><dd>{digest.source.through_event_sequence}</dd></div>
          <div><dt>Transcript</dt><dd>{shortContextHash(digest.source.transcript_checkpoint_hash)}</dd></div>
          <div><dt>Digest</dt><dd>{shortContextHash(digest.content_hash)}</dd></div>
        </dl>
      </div>
    </details>
  );
}

export function ContextRecoveryPanel({ threadId }: { threadId: string }) {
  const [open, setOpen] = useState(false);
  const [overview, setOverview] = useState<SessionContextOverview | null>(null);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");
  const [action, setAction] = useState<"idle" | "confirm-rebase" | "confirm-rollback" | "running">("idle");
  const [notice, setNotice] = useState("");
  const runView = useRunViewModel();
  const panelRef = useRef<HTMLElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);

  useDialogFocus({
    open,
    panelRef,
    initialFocusRef: closeButtonRef,
    onEscape: () => setOpen(false),
  });

  async function refresh() {
    setLoading(true);
    try {
      setOverview(await loadThreadContext(threadId));
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "上下文状态暂不可用");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!threadId) {
      setOverview(null);
      setError("");
      setLoading(false);
      return;
    }
    void refresh();
    // Refresh after every terminal phase so a newly published Digest appears
    // without polling while the model is running.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [threadId, runView?.phase]);

  useEffect(() => {
    if (!open) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, [open]);

  async function loadMore() {
    if (!overview?.next_before_version) return;
    setLoadingMore(true);
    try {
      const next = await loadThreadContext(threadId, overview.next_before_version);
      if (next) setOverview((current) => current ? mergeContextPages(current, next) : next);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "恢复点加载失败");
    } finally {
      setLoadingMore(false);
    }
  }

  async function mutateContext(operation: "rebase" | "rollback") {
    setAction("running");
    setNotice("");
    try {
      if (operation === "rebase") {
        await rebaseThreadContext(threadId);
        setNotice("已从恢复点建立新会话；旧会话保留，可切回。");
      } else {
        await rollbackThreadContextRebase(threadId);
        setNotice("已恢复重建前的完整会话上下文。");
      }
      await refresh();
      setAction("idle");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "上下文操作失败");
      setAction("idle");
    }
  }

  const state = overview?.state;
  const windowSnapshot = overview?.window;
  const trust = state?.trust_high_watermark ?? "safe";
  return (
    <>
      <button
        className="icon-button context-recovery-trigger"
        type="button"
        onClick={() => setOpen(true)}
        aria-label="上下文与压缩"
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls="context-recovery-panel"
      >
        <span className={`context-recovery-dot trust-${trust}`} aria-hidden="true" />
        <span>上下文</span>
        {state?.latest_digest_version ? <small>v{state.latest_digest_version}</small> : null}
      </button>
      {open ? createPortal(
        <div
          className="context-recovery-backdrop"
          role="presentation"
          onMouseDown={(event) => {
            if (event.target === event.currentTarget) setOpen(false);
          }}
        >
          <aside
            ref={panelRef}
            id="context-recovery-panel"
            className="context-recovery-panel"
            role="dialog"
            aria-modal="true"
            aria-labelledby="context-recovery-title"
          >
            <header>
              <div>
                <p>SESSION CONTEXT</p>
                <h2 id="context-recovery-title">上下文与压缩</h2>
              </div>
              <button
                ref={closeButtonRef}
                type="button"
                onClick={() => setOpen(false)}
                aria-label="关闭上下文面板"
              >
                ×
              </button>
            </header>
            <div className="context-recovery-body">
              <ContextCompactionHistory key={threadId} threadId={threadId} />
              {windowSnapshot ? (
                <section
                  className={`context-window-card level-${windowSnapshot.level}`}
                  aria-label="模型上下文窗口"
                >
                  <header>
                    <div>
                      <span>PROVIDER WINDOW · 上一轮</span>
                      <strong>{contextBudgetLabels[windowSnapshot.level]}</strong>
                    </div>
                    <b>{windowSnapshot.percentage.toFixed(1)}%</b>
                  </header>
                  <div
                    className="context-window-meter"
                    role="progressbar"
                    aria-label="上下文窗口占用"
                    aria-valuemin={0}
                    aria-valuemax={100}
                    aria-valuenow={Math.round(windowSnapshot.percentage)}
                  >
                    <i style={{ width: `${Math.max(1, windowSnapshot.percentage)}%` }} />
                    <span style={{ left: `${windowSnapshot.soft_threshold_percentage}%` }} title="软阈值" />
                    <span style={{ left: `${windowSnapshot.hard_threshold_percentage}%` }} title="硬阈值" />
                  </div>
                  <dl>
                    <div><dt>已使用</dt><dd>{formatTokens(windowSnapshot.total_tokens)}</dd></div>
                    <div><dt>剩余</dt><dd>{formatTokens(windowSnapshot.headroom_tokens)}</dd></div>
                    <div><dt>模型窗口</dt><dd>{formatTokens(windowSnapshot.max_tokens)}</dd></div>
                  </dl>
                  <p>{contextBudgetGuidance[windowSnapshot.level]}</p>
                  <footer>
                    <span>{windowSnapshot.model || "当前模型"}</span>
                    <span>
                      软阈值 {windowSnapshot.soft_threshold_percentage.toFixed(0)}% · 硬阈值 {windowSnapshot.hard_threshold_percentage.toFixed(0)}%
                    </span>
                  </footer>
                </section>
              ) : (
                <p className="context-history-note">精确窗口用量尚未返回；上方仅展示历史文本字符数。</p>
              )}
              {(overview?.digests.length || overview?.rebase_supported || overview?.rollback_supported) ? (
                <details className="context-maintenance">
                  <summary>高级维护 · 故障恢复</summary>
                  <p className="context-history-note">用于会话异常时从恢复点重建或切回旧会话，不是撤销自动压缩。正常对话无需操作。</p>
                  {overview?.rebase_supported || overview?.rollback_supported ? (
                    <section className="context-recovery-actions" aria-label="上下文重建与恢复">
                      <div>
                        <strong>上下文维护</strong>
                        <span>
                          {overview.previous_session_count
                            ? `已保留 ${overview.previous_session_count} 个完整历史会话`
                            : "从恢复点重建会话，仅用于异常恢复"}
                        </span>
                      </div>
                      <div className="context-recovery-action-buttons">
                        {overview.rebase_supported ? (
                          <button
                            type="button"
                            onClick={() => setAction("confirm-rebase")}
                            disabled={action === "running"}
                          >
                            从恢复点重建
                          </button>
                        ) : null}
                        {overview.rollback_supported ? (
                          <button
                            className="is-secondary"
                            type="button"
                            onClick={() => setAction("confirm-rollback")}
                            disabled={action === "running"}
                          >
                            回到重建前
                          </button>
                        ) : null}
                      </div>
                      {action === "confirm-rebase" ? (
                        <div className="context-recovery-confirm" role="alert">
                          <p>将以当前恢复点建立新会话。这不是模型对完整历史的摘要压缩，恢复点可能只包含最近一轮的信息。原会话保留，可随时切回。</p>
                          <div>
                            <button type="button" onClick={() => void mutateContext("rebase")}>确认重建</button>
                            <button className="is-secondary" type="button" onClick={() => setAction("idle")}>取消</button>
                          </div>
                        </div>
                      ) : null}
                      {action === "confirm-rollback" ? (
                        <div className="context-recovery-confirm" role="alert">
                          <p>将重新绑定到重建前的完整会话。当前新会话同样会保留。</p>
                          <div>
                            <button type="button" onClick={() => void mutateContext("rollback")}>确认恢复</button>
                            <button className="is-secondary" type="button" onClick={() => setAction("idle")}>取消</button>
                          </div>
                        </div>
                      ) : null}
                    </section>
                  ) : null}
                  {notice ? <p className="context-recovery-notice" role="status">{notice}</p> : null}
                  {overview?.digests.map((digest, index) => (
                    <RecoveryPoint key={digest.digest_id} digest={digest} latest={index === 0} />
                  ))}
                  {overview?.next_before_version ? (
                    <button
                      className="context-recovery-more"
                      type="button"
                      onClick={() => void loadMore()}
                      disabled={loadingMore}
                    >
                      {loadingMore ? "正在加载…" : "加载更早恢复点"}
                    </button>
                  ) : null}
                </details>
              ) : null}
              {loading && !overview ? <p className="context-history-note">正在读取上下文状态…</p> : null}
              {error ? <p role="alert">{error} <button type="button" onClick={() => void refresh()}>重试</button></p> : null}
            </div>
          </aside>
        </div>,
        document.body,
      ) : null}
    </>
  );
}
