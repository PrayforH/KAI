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
import { useDialogFocus } from "../lib/use-dialog-focus";

const contextBudgetLabels: Record<ContextBudgetLevel, string> = {
  green: "窗口充足",
  watch: "接近软阈值",
  compact_ready: "建议压缩",
  emergency: "需要立即处理",
};

const contextBudgetGuidance: Record<ContextBudgetLevel, string> = {
  green: "当前上下文空间充足，可继续对话。",
  watch: "建议减少非必要上下文注入；复杂任务完成后可考虑压缩。",
  compact_ready: "建议在没有运行中任务时建立精简会话，完整历史仍会保留。",
  emergency: "窗口余量很低，请在当前任务结束后立即压缩，避免下一轮触及模型上限。",
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
        setNotice("已切换到精简上下文；旧会话完整保留，可随时回滚。");
      } else {
        await rollbackThreadContextRebase(threadId);
        setNotice("已恢复压缩前的完整会话上下文。");
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
        aria-label="上下文与恢复点"
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
                <h2 id="context-recovery-title">上下文与恢复点</h2>
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
              <section className="context-recovery-overview">
                <div>
                  <span>信任水位</span>
                  <strong className={`trust-${trust}`}>{contextTrustLabels[trust]}</strong>
                </div>
                <div>
                  <span>恢复点</span>
                  <strong>{state?.latest_digest_version ?? 0}</strong>
                </div>
                <div>
                  <span>状态版本</span>
                  <strong>{state?.revision ?? "—"}</strong>
                </div>
              </section>
              <p className="context-recovery-explainer">
                恢复点只保存脱敏事实与耐久对象引用；原始 transcript 仍由 SDK 管理，不会在此展示。
              </p>
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
              ) : overview?.window_status?.status === "unavailable" ? (
                <div className="context-window-unavailable is-unsupported" role="status">
                  <strong>精确窗口观测不可用</strong>
                  <span>
                    当前运行时未返回 SDK token 窗口；仍由 SDK 原生自动压缩保护，平台不会用字符数估算替代。
                  </span>
                </div>
              ) : (
                <div className="context-window-unavailable">
                  <strong>等待精确窗口观测</strong>
                  <span>
                    同一会话完成下一轮模型任务后尝试读取；系统不会用字符数冒充 token。
                  </span>
                </div>
              )}
              {overview?.rebase_supported || overview?.rollback_supported ? (
                <section className="context-recovery-actions" aria-label="上下文压缩与恢复">
                  <div>
                    <strong>上下文维护</strong>
                    <span>
                      {overview.previous_session_count
                        ? `已保留 ${overview.previous_session_count} 个完整历史会话`
                        : "建立精简会话，降低后续长对话负担"}
                    </span>
                  </div>
                  <div className="context-recovery-action-buttons">
                    {overview.rebase_supported ? (
                      <button
                        type="button"
                        onClick={() => setAction("confirm-rebase")}
                        disabled={action === "running"}
                      >
                        压缩上下文
                      </button>
                    ) : null}
                    {overview.rollback_supported ? (
                      <button
                        className="is-secondary"
                        type="button"
                        onClick={() => setAction("confirm-rollback")}
                        disabled={action === "running"}
                      >
                        回到压缩前
                      </button>
                    ) : null}
                  </div>
                  {action === "confirm-rebase" ? (
                    <div className="context-recovery-confirm" role="alert">
                      <p>将以当前恢复点建立新会话。事实、决定、待办与工作区引用会保留，原会话不会删除。</p>
                      <div>
                        <button type="button" onClick={() => void mutateContext("rebase")}>确认压缩</button>
                        <button className="is-secondary" type="button" onClick={() => setAction("idle")}>取消</button>
                      </div>
                    </div>
                  ) : null}
                  {action === "confirm-rollback" ? (
                    <div className="context-recovery-confirm" role="alert">
                      <p>将重新绑定到压缩前的完整会话。当前精简会话同样会保留。</p>
                      <div>
                        <button type="button" onClick={() => void mutateContext("rollback")}>确认恢复</button>
                        <button className="is-secondary" type="button" onClick={() => setAction("idle")}>取消</button>
                      </div>
                    </div>
                  ) : null}
                </section>
              ) : null}
              {notice ? <p className="context-recovery-notice" role="status">{notice}</p> : null}
              {loading && !overview ? <p className="context-recovery-empty">正在读取上下文状态…</p> : null}
              {error ? (
                <div className="context-recovery-error" role="alert">
                  <span>{error}</span>
                  <button type="button" onClick={() => void refresh()}>重试</button>
                </div>
              ) : null}
              {!loading && !error && (!overview || overview.digests.length === 0) ? (
                <div className="context-recovery-empty">
                  <strong>还没有恢复点</strong>
                  <span>完成首轮真实模型任务后，系统会在成功终态前自动生成。</span>
                </div>
              ) : null}
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
            </div>
          </aside>
        </div>,
        document.body,
      ) : null}
    </>
  );
}
