"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import { createPortal } from "react-dom";
import {
  allFiltersEnabled,
  buildSessionTrace,
  extractSessionRuns,
  filterTraceNodes,
  formatClock,
  formatDuration,
  buildStripLayout,
  searchTraceNodes,
  TRACE_FILTER_GROUPS,
  traceFilterKey,
  type SessionTrace,
  type TraceNode,
} from "../lib/session-trace";
import type { RunActivity } from "../lib/activity-schema";
import {
  loadFullThreadHistory,
  type ThreadHistoryResponse,
} from "../lib/task-history";
import {
  fetchAgentManifestSummary,
  type AgentManifestSummary,
} from "../lib/agent-manifest-summary";
import {
  loadThreadContext,
  type SessionContextOverview,
} from "../lib/context-client";
import styles from "./run-trace-console.module.css";

type DetailTab = "overview" | "prompt" | "entries" | "arguments" | "output" | "timing";

const TAB_LABELS: ReadonlyArray<readonly [DetailTab, string]> = [
  ["overview", "概述"],
  ["arguments", "参数"],
  ["output", "结果"],
  ["timing", "计时"],
];

function nodeTabs(node: TraceNode): ReadonlyArray<readonly [DetailTab, string]> {
  if (node.badge === "系统" || node.entries?.length) {
    const tabs: ReadonlyArray<readonly [DetailTab, string]> = [
      ["overview", "概述"],
      ...(node.systemPrompt ? [["prompt", "系统提示词"] as const] : []),
      ...(node.entries?.length
        ? [["entries", node.badge === "系统" ? "工具" : "技能"] as const]
        : []),
      ["timing", "计时"] as const,
    ];
    return tabs;
  }
  if (node.lane === "input" || !node.argumentsText) {
    return TAB_LABELS.filter(([tab]) => tab !== "arguments");
  }
  return TAB_LABELS;
}

function DownloadIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="M10 3.5v8m0 0 3.2-3.2M10 11.5 6.8 8.3M4 14.5v1a1 1 0 0 0 1 1h10a1 1 0 0 0 1-1v-1" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 20 20" aria-hidden="true">
      <path d="m5.5 5.5 9 9m0-9-9 9" />
    </svg>
  );
}

function statusClass(
  stylesMap: Record<string, string>,
  status: string,
  running: boolean,
): string {
  if (running) return stylesMap["is-running"];
  if (["failed", "rejected", "timed_out", "error"].includes(status)) {
    return stylesMap["is-failed"];
  }
  if (["cancelled", "cancelling"].includes(status)) return stylesMap["is-cancelled"];
  return stylesMap["is-ok"];
}

export function formatTokenCount(value: number): string {
  if (!Number.isFinite(value) || value <= 0) return "0";
  if (value < 1_000) return String(value);
  if (value < 1_000_000) return `${(value / 1_000).toFixed(1)}k`;
  return `${(value / 1_000_000).toFixed(2)}M`;
}

function downloadSessionLog(trace: SessionTrace, threadId: string) {
  const payload = {
    thread_id: threadId,
    exported_at: new Date().toISOString(),
    summary: trace.summary,
    window: trace.window,
    runs: trace.runs.map((run) => ({
      run_id: run.runId,
      turn: run.turn,
      prompt: run.prompt,
      activity: run.activity,
    })),
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = `session-${threadId || "trace"}-log.json`;
  anchor.click();
  URL.revokeObjectURL(url);
}

export function RunTraceConsole({
  threadId,
  liveActivity,
  runBusy = false,
  onBack,
  agentName,
  agentVersion,
}: {
  threadId: string;
  liveActivity?: RunActivity | null;
  /** True while the task's current run is queued/running; a fall to false
   * triggers one history refetch so the finished turn shows up. */
  runBusy?: boolean;
  /** Return to the conversation view (the trace view replaces it in place). */
  onBack?: () => void;
  /** The task's agent version; resolves the 系统 node's prompt and tools. */
  agentName?: string;
  agentVersion?: string;
}) {
  const [history, setHistory] = useState<ThreadHistoryResponse["messages"] | null>(
    null,
  );
  const [historyReady, setHistoryReady] = useState(false);
  const [manifest, setManifest] = useState<AgentManifestSummary | null>(null);
  const [manifestReady, setManifestReady] = useState(false);
  const [threadContext, setThreadContext] = useState<SessionContextOverview | null>(null);
  const [contextState, setContextState] = useState<"idle" | "loading" | "ready" | "unavailable">("idle");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [reloadNonce, setReloadNonce] = useState(0);
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [hovered, setHovered] = useState<{ id: string; x: number; y: number } | null>(null);
  const [timelineZoom, setTimelineZoom] = useState<1 | 2 | 4>(1);
  // Pinch (ctrl+wheel) magnifies around the cursor: keep the time under the
  // pointer fixed by re-anchoring scrollLeft after the zoom step.
  const timelineRef = useRef<HTMLDivElement>(null);
  const zoomAnchor = useRef<{ fraction: number; cursorX: number } | null>(null);
  useEffect(() => {
    const anchor = zoomAnchor.current;
    const container = timelineRef.current;
    if (!anchor || !container) return;
    zoomAnchor.current = null;
    const contentWidth = container.scrollWidth;
    container.scrollLeft = Math.max(0, anchor.fraction * contentWidth - anchor.cursorX);
  }, [timelineZoom]);
  useEffect(() => {
    const container = timelineRef.current;
    if (!container) return;
    const onWheel = (event: WheelEvent) => {
      if (!event.ctrlKey) return;
      event.preventDefault();
      const rect = container.getBoundingClientRect();
      const cursorX = event.clientX - rect.left;
      const contentWidth = container.scrollWidth;
      zoomAnchor.current = {
        fraction: (container.scrollLeft + cursorX) / Math.max(contentWidth, 1),
        cursorX,
      };
      setTimelineZoom((current) => {
        const steps: Array<1 | 2 | 4> = event.deltaY < 0 ? [1, 2, 4] : [4, 2, 1];
        const index = steps.indexOf(current);
        return index >= 0 && index < steps.length - 1 ? steps[index + 1] : current;
      });
    };
    container.addEventListener("wheel", onWheel, { passive: false });
    return () => container.removeEventListener("wheel", onWheel);
  }, []);
  const [activeTab, setActiveTab] = useState<DetailTab>("overview");
  const [filters, setFilters] = useState<Record<string, boolean>>(allFiltersEnabled);
  const [filterOpen, setFilterOpen] = useState(false);
  const filterRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLOListElement>(null);
  const scrolledForSelection = useRef<string | null>(null);

  // Type toggles persist per browser so an operator's preferred view sticks.
  useEffect(() => {
    try {
      const stored = window.localStorage.getItem("harness-trace-filters");
      if (stored) setFilters({ ...allFiltersEnabled(), ...JSON.parse(stored) as Record<string, boolean> });
    } catch {
      // Defaults are fine when storage is unavailable.
    }
  }, []);
  const setFilter = useCallback((key: string, value: boolean) => {
    setFilters((current) => {
      const next = { ...current, [key]: value };
      try {
        window.localStorage.setItem("harness-trace-filters", JSON.stringify(next));
      } catch {
        // Ignore storage failures; the toggle still applies for this session.
      }
      return next;
    });
  }, []);
  useEffect(() => {
    if (!filterOpen) return;
    function onPointerDown(event: PointerEvent) {
      if (!filterRef.current?.contains(event.target as Node)) setFilterOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") setFilterOpen(false);
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [filterOpen]);

  // A thread created moments ago has no durable binding yet: its history
  // returns 404 until the first run is accepted. That is "no trace yet", not
  // a failure — retry while a run is in flight so the turn shows up.
  let lastStatus = 0;
  useEffect(() => {
    if (!threadId) {
      setHistory(null);
      setHistoryReady(false);
      setError("");
      return;
    }
    let active = true;
    setHistoryReady(false);
    setLoading(true);
    setError("");
    loadFullThreadHistory(threadId, {
      onNotFound: () => {
        lastStatus = 404;
      },
    })
      .then((messages) => {
        if (active) {
          setHistory(messages);
          setHistoryReady(true);
        }
      })
      .catch((cause: unknown) => {
        if (active && lastStatus !== 404) {
          setError(cause instanceof Error ? cause.message : String(cause));
          setHistoryReady(true);
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [threadId, reloadNonce]);

  const liveRunId = liveActivity?.run_id ?? null;

  // A new run starting (steer/queued turn) is worth one refetch.
  const wasLiveRunId = useRef(liveRunId);
  useEffect(() => {
    if (wasLiveRunId.current && liveRunId && wasLiveRunId.current !== liveRunId) {
      setReloadNonce((value) => value + 1);
    }
    wasLiveRunId.current = liveRunId;
  }, [liveRunId]);

  // Resolve the versioned system prompt / tool list once per agent version.
  useEffect(() => {
    if (!agentName || !agentVersion) {
      setManifest(null);
      setManifestReady(true);
      return;
    }
    let active = true;
    setManifestReady(false);
    fetchAgentManifestSummary(agentName, agentVersion).then((summary) => {
      if (active) {
        setManifest(summary);
        setManifestReady(true);
      }
    });
    return () => {
      active = false;
    };
  }, [agentName, agentVersion]);

  const trace = useMemo(
    () =>
      buildSessionTrace(
        history ? extractSessionRuns(history) : [],
        liveActivity ?? undefined,
        manifest ?? undefined,
      ),
    [history, liveActivity, manifest],
  );

  // While the task runs (or a live activity is on screen) and no durable run
  // has been parsed yet, poll through the window where history may only hold
  // the raw user message. Parsed runs end the polling; the busy→idle
  // transition then does the final refetch.
  useEffect(() => {
    if (!threadId || (!runBusy && !liveRunId)) return;
    if (trace.runs.length > 0) return;
    const timer = globalThis.setInterval(() => {
      setReloadNonce((value) => value + 1);
    }, 3_000);
    return () => globalThis.clearInterval(timer);
  }, [threadId, runBusy, liveRunId, trace.runs.length]);
  const visibleNodes = useMemo(
    () => searchTraceNodes(filterTraceNodes(trace.nodes, filters), query),
    [trace.nodes, filters, query],
  );
  // Timeline bars represent phases, not instants. System/context/user markers
  // have zero duration and remain in the event stream/detail view only; drawing
  // them with a minimum width made the bar look like uninterrupted dots.
  // DSH strip: the 输入 lane shows only user-input ticks; context and other
  // instant rows stay in the event stream. Every other lane draws phases with
  // real duration.
  const timelineNodes = useMemo(
    () =>
      visibleNodes.filter(
        (node) =>
          (node.lane === "input" && node.badge === "用户") || node.endMs > node.startMs,
      ),
    [visibleNodes],
  );
  // Gapless strip: blocks are laid out in phase order (adjacent in time touch),
  // input ticks map through the same anchors.
  const strip = useMemo(() => {
    const phases = timelineNodes.filter((node) => node.lane !== "input");
    const ticks = timelineNodes.filter((node) => node.lane === "input");
    return buildStripLayout(phases, ticks);
  }, [timelineNodes]);
  const stripBlocks = useMemo(
    () => new Map(strip.blocks.map((entry) => [entry.id, entry])),
    [strip.blocks],
  );
  const stripTicks = useMemo(
    () => new Map(strip.ticks.map((entry) => [entry.id, entry])),
    [strip.ticks],
  );
  const selected = useMemo(
    () => trace.nodes.find((node) => node.id === selectedId) ?? null,
    [trace.nodes, selectedId],
  );

  // The durable 上下文 rows carry only counts; the drawer fills them with the
  // session's context read model (window snapshot + digest entries), loaded
  // once per thread the first time a context row is inspected. The in-flight
  // guard lives in a ref: a state-based flag would re-run this effect on
  // "loading" and invalidate the pending fetch's cleanup flag.
  const contextLoadRef = useRef<"idle" | "loading" | "done">("idle");
  useEffect(() => {
    if (!selected || selected.badge !== "上下文" || contextLoadRef.current !== "idle" || !threadId) return;
    contextLoadRef.current = "loading";
    setContextState("loading");
    loadThreadContext(threadId)
      .then((overview) => {
        contextLoadRef.current = "done";
        setThreadContext(overview);
        setContextState("ready");
      })
      .catch(() => {
        contextLoadRef.current = "done";
        setContextState("unavailable");
      });
  }, [selected, threadId]);

  const selectNode = useCallback((node: TraceNode) => {
    setSelectedId((current) => (current === node.id ? null : node.id));
    setActiveTab("overview");
    scrolledForSelection.current = null;
  }, []);

  // The active turn is merged from the live store; once the run settles, one
  // refetch replaces it with the durable history projection (and picks up any
  // turns that completed while the tab was open).
  const wasBusy = useRef(runBusy);
  useEffect(() => {
    if (wasBusy.current && !runBusy) setReloadNonce((value) => value + 1);
    wasBusy.current = runBusy;
  }, [runBusy]);

  useEffect(() => {
    if (!selected) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setSelectedId(null);
        return;
      }
      if (event.key === "ArrowUp" || event.key === "ArrowDown") {
        const index = visibleNodes.findIndex((node) => node.id === selected.id);
        if (index < 0) return;
        const next = event.key === "ArrowUp" ? index - 1 : index + 1;
        if (next >= 0 && next < visibleNodes.length) {
          event.preventDefault();
          selectNode(visibleNodes[next]);
        }
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [selected, visibleNodes, selectNode]);

  // Clicking a timeline segment selects the node; the list scrolls once so the
  // row is visible without fighting later re-renders.
  useEffect(() => {
    if (!selected || scrolledForSelection.current === selected.id) return;
    const row = listRef.current?.querySelector(
      `[data-node-id="${CSS.escape(selected.id)}"]`,
    );
    row?.scrollIntoView({ block: "nearest" });
    scrolledForSelection.current = selected.id;
  }, [selected]);

  useEffect(() => {
    if (!selectedId) return;
    if (!trace.nodes.some((node) => node.id === selectedId)) setSelectedId(null);
  }, [trace.nodes, selectedId]);

  const lanes: ReadonlyArray<readonly [string, TraceNode["lane"]]> = [
    ["输入", "input"],
    ["模型", "model"],
    ["工具", "tool"],
  ];

  if (!historyReady || !manifestReady) {
    return (
      <div className={styles.console} aria-label="调用轨迹" aria-busy="true">
        <div className={styles.toolbar}>
          <div className={styles.chips}>
            <span className={styles.loading}>正在读取完整轨迹…</span>
          </div>
          <div className={styles.traceSkeleton} aria-hidden="true" />
        </div>
        <div className={styles.traceLoadingBody}>
          <span />
          <span />
          <span />
        </div>
      </div>
    );
  }

  return (
    <div className={styles.console} aria-label="调用轨迹">
      <div className={styles.toolbar}>
        <div className={styles.chips} aria-label="轨迹摘要">
          <span className={styles.chip}>
            <strong>{formatDuration(trace.summary.durationMs)}</strong>时长
          </span>
          <span className={styles.chip}>
            <strong>{trace.summary.turns}</strong>轮次
          </span>
          <span className={styles.chip}>
            <strong>{trace.summary.toolCalls}</strong>调用
          </span>
          {trace.usage.known && (
            <span
              className={styles.chip}
              title={`输入 ${trace.usage.inputTokens} · 输出 ${trace.usage.outputTokens} tokens`}
            >
              <strong>{formatTokenCount(trace.usage.inputTokens + trace.usage.outputTokens)}</strong>tokens
            </span>
          )}
          {loading && <span className={styles.loading}>加载中…</span>}
          {error && (
            <span className={styles.error} role="alert">加载失败：{error}</span>
          )}
        </div>
        <div className={styles.toolbarActions}>
          {onBack && (
            <button type="button" className={styles.backButton} onClick={onBack}>
              <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M12.5 4.5 7 10l5.5 5.5" /></svg>
              对话
            </button>
          )}
          <input className={styles.search} type="search" placeholder="搜索" aria-label="搜索轨迹" value={query} onChange={(event) => setQuery(event.target.value)} />
          <div className={styles.filterWrap} ref={filterRef}>
            <button type="button" className={styles.iconButton} aria-label="筛选事件类型" aria-expanded={filterOpen} title="筛选事件类型" onClick={() => setFilterOpen((value) => !value)}>
              <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M3.5 6h13M3.5 10h13M3.5 14h13" /><circle cx="7.5" cy="6" r="1.7" fill="var(--surface)" /><circle cx="12.5" cy="10" r="1.7" fill="var(--surface)" /><circle cx="8.5" cy="14" r="1.7" fill="var(--surface)" /></svg>
            </button>
            {filterOpen && (
              <div className={styles.filterPanel} role="dialog" aria-label="筛选事件类型">
                {TRACE_FILTER_GROUPS.map((group) => (
                  <label key={group.key} className={styles.filterRow}><span>{group.label}</span><input type="checkbox" role="switch" checked={filters[group.key] !== false} onChange={(event) => setFilter(group.key, event.target.checked)} /></label>
                ))}
              </div>
            )}
          </div>
          <button type="button" className={styles.iconButton} aria-label="刷新轨迹" title="刷新轨迹" onClick={() => setReloadNonce((value) => value + 1)}><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M15.5 10a5.5 5.5 0 1 1-1.6-3.9M15.5 3.5v3h-3" /></svg></button>
          <button type="button" className={styles.iconButton} aria-label="下载 Session 日志" title="下载 Session 日志" disabled={!trace.nodes.length} onClick={() => downloadSessionLog(trace, threadId)}><DownloadIcon /></button>
        </div>
      </div>

      <div
        ref={timelineRef}
        className={styles.timeline}
        aria-hidden={trace.window.totalMs <= 0}
        style={{ "--trace-zoom": timelineZoom } as CSSProperties}
      >
        {trace.window.totalMs > 0 && timelineNodes.length === 0 && (
          <div className={styles.timelineEmpty}>
            暂无可量化的持续阶段（瞬时事件见下方事件流）
          </div>
        )}
        {trace.window.totalMs > 0 &&
          lanes.map(([label, lane]) => (
            <div className={styles.lane} key={lane}>
              <span className={styles.laneLabel}>{label}</span>
              <div className={styles.laneTrack}>
                {timelineNodes
                  .filter((node) => node.lane === lane)
                  .map((node) => {
                    const position = lane === "input"
                      ? stripTicks.get(node.id)
                      : stripBlocks.get(node.id);
                    if (!position) return null;
                    return (
                      <button
                        key={node.id}
                        type="button"
                        className={`${styles.block} ${styles[`lane-${node.lane}`]} ${statusClass(styles, node.status, node.running)}${selectedId === node.id ? ` ${styles["is-selected"]}` : ""}`}
                        style={{
                          left: `${position.left}%`,
                          width: position.width === undefined ? "4px" : `${position.width}%`,
                        }}
                        aria-label={`${node.badge} ${node.label}`}
                        onMouseEnter={(event) => setHovered({ id: node.id, x: event.clientX, y: event.clientY })}
                        onMouseLeave={() => setHovered(null)}
                        onFocus={(event) => {
                          const rect = event.currentTarget.getBoundingClientRect();
                          setHovered({ id: node.id, x: rect.left, y: rect.top });
                        }}
                        onBlur={() => setHovered(null)}
                        onClick={() => selectNode(node)}
                      />
                    );
                  })}
              </div>
            </div>
          ))}
      </div>

      {hovered && (() => {
        const node = timelineNodes.find((item) => item.id === hovered.id);
        if (!node) return null;
        const clampedX = Math.min(hovered.x + 12, window.innerWidth - 330);
        const clampedY = Math.min(hovered.y + 14, window.innerHeight - 110);
        return createPortal(
          <div
            className={styles.timelineTooltip}
            style={{ left: clampedX, top: clampedY }}
            role="tooltip"
          >
            <strong>{node.badge} · {node.label}</strong>
            <span>{formatClock(node.startMs)} → {formatClock(node.endMs)} · {formatDuration(node.endMs - node.startMs)}</span>
            {node.detail && <em>{node.detail}</em>}
          </div>,
          document.body,
        );
      })()}

      <div className={styles.body}>
        <ol className={styles.list} ref={listRef} aria-label="轨迹事件">
          {visibleNodes.map((node, index) => {
            const previous = visibleNodes[index - 1];
            const showTurn = !previous || previous.turn !== node.turn;
            return (
              <li key={node.id} data-node-id={node.id}>
                {showTurn && (
                  <div className={styles.turnDivider} role="presentation">
                    第 {node.turn} 轮
                  </div>
                )}
                <button
                  type="button"
                  className={`${styles.row}${selectedId === node.id ? ` ${styles["is-selected"]}` : ""}${node.running ? "" : ["failed", "rejected", "timed_out", "error"].includes(node.status) ? ` ${styles["is-failed-row"]}` : ""}`}
                  onClick={() => selectNode(node)}
                >
                  <span
                    className={`${styles.dot} ${statusClass(styles, node.status, node.running)}`}
                    aria-hidden="true"
                  />
                  <span className={`${styles.badge} ${node.badge === "思考" ? styles["lane-thinking"] : styles[`lane-${node.lane}`]}`}>
                    {node.badge}
                  </span>
                  <span className={styles.rowMain}>
                    <span className={styles.rowLabel}>{node.label}</span>
                    {node.detail && (
                      <span className={styles.rowDetail}>{node.detail}</span>
                    )}
                  </span>
                  <span className={styles.rowMeta}>
                    {node.endMs > node.startMs && (
                      <span className={styles.rowDuration}>
                        {formatDuration(node.endMs - node.startMs)}
                      </span>
                    )}
                    <span className={styles.rowClock}>{formatClock(node.startMs)}</span>
                  </span>
                </button>
              </li>
            );
          })}
          {!loading && !visibleNodes.length && (
            <li className={styles.empty}>
              <strong>{query ? "没有匹配的轨迹事件" : "还没有可展示的轨迹"}</strong>
              <p>
                {query
                  ? "换个关键词试试"
                  : "任务运行后，这里会按轮次展示输入、模型与工具调用。"}
              </p>
            </li>
          )}
        </ol>

        {selected && (
          <aside className={styles.detail} aria-label="轨迹详情">
            <header className={styles.detailHeader}>
              <span className={styles.badge}>{selected.badge}</span>
              <span className={styles.detailContext}>
                第 {selected.turn} 轮 · 步骤 {selected.step}
              </span>
              <div className={styles.stepNav}>
                <button
                  type="button"
                  className={styles.iconButton}
                  aria-label="上一步"
                  title="上一步 (↑)"
                  disabled={visibleNodes.findIndex((node) => node.id === selected.id) <= 0}
                  onClick={() => {
                    const index = visibleNodes.findIndex((node) => node.id === selected.id);
                    if (index > 0) selectNode(visibleNodes[index - 1]);
                  }}
                >
                  <svg viewBox="0 0 20 20" aria-hidden="true"><path d="m5 12 5-5 5 5" /></svg>
                </button>
                <button
                  type="button"
                  className={styles.iconButton}
                  aria-label="下一步"
                  title="下一步 (↓)"
                  disabled={visibleNodes.findIndex((node) => node.id === selected.id) >= visibleNodes.length - 1}
                  onClick={() => {
                    const index = visibleNodes.findIndex((node) => node.id === selected.id);
                    if (index >= 0 && index < visibleNodes.length - 1) {
                      selectNode(visibleNodes[index + 1]);
                    }
                  }}
                >
                  <svg viewBox="0 0 20 20" aria-hidden="true"><path d="m5 8 5 5 5-5" /></svg>
                </button>
              </div>
              <button
                type="button"
                className={styles.iconButton}
                aria-label="关闭详情"
                onClick={() => setSelectedId(null)}
              >
                <CloseIcon />
              </button>
            </header>
            <div className={styles.detailTabs} role="tablist">
              {nodeTabs(selected).map(([tab, label]) => (
                <button
                  key={tab}
                  type="button"
                  role="tab"
                  aria-selected={activeTab === tab}
                  className={activeTab === tab ? styles["is-active"] : undefined}
                  onClick={() => setActiveTab(tab)}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className={styles.detailBody} role="tabpanel">
              {activeTab === "overview" && (
                <div className={styles.overview}>
                  <p className={styles.overviewTitle}>{selected.label}</p>
                  {selected.summary && <p>{selected.summary}</p>}
                  <dl>
                    <div>
                      <dt>状态</dt>
                      <dd>{selected.running ? "运行中" : selected.status}</dd>
                    </div>
                    {selected.artifact && (
                      <div>
                        <dt>产物</dt>
                        <dd>
                          <a
                            href="#"
                            onClick={(event) => {
                              event.preventDefault();
                              window.dispatchEvent(
                                new CustomEvent("harness:preview-artifact", {
                                  detail: { artifact_id: selected.artifact?.id },
                                }),
                              );
                            }}
                          >
                            {selected.artifact.name}
                          </a>
                        </dd>
                      </div>
                    )}
                  </dl>

                  {selected.badge === "用户" && onBack && (
                    <p className={styles.overviewLink}>
                      <a
                        href="#"
                        onClick={(event) => {
                          event.preventDefault();
                          onBack();
                        }}
                      >
                        在对话中查看 ↗
                      </a>
                    </p>
                  )}

                  {selected.argumentsText && (
                    <details className={styles.section} open>
                      <summary>参数</summary>
                      <pre className={styles.code}>{selected.argumentsText}</pre>
                    </details>
                  )}
                  {(selected.output || selected.detail) && (
                    <details className={styles.section} open>
                      <summary>结果</summary>
                      <pre className={styles.code}>
                        {selected.output ?? selected.detail ?? "（无输出）"}
                      </pre>
                    </details>
                  )}
                  {selected.citations?.length ? (
                    <details className={styles.section}>
                      <summary>来源引用（{selected.citations.length}）</summary>
                      <ol className={styles.citationList}>
                        {selected.citations.map((citation) => (
                          <li key={`${citation.index}-${citation.sourceReference}`}>
                            <span>{citation.title || citation.sourceReference}</span>
                            {typeof citation.score === "number" && (
                              <small>score {citation.score.toFixed(3)}</small>
                            )}
                          </li>
                        ))}
                      </ol>
                    </details>
                  ) : null}
                  <details className={styles.section}>
                    <summary>计时</summary>
                    <dl className={styles.timing}>
                      <div><dt>开始</dt><dd>{formatClock(selected.startMs)}</dd></div>
                      <div><dt>结束</dt><dd>{formatClock(selected.endMs)}</dd></div>
                      <div><dt>耗时</dt><dd>{formatDuration(selected.endMs - selected.startMs)}</dd></div>
                      <div><dt>轮次</dt><dd>第 {selected.turn} 轮 · 步骤 {selected.step}</dd></div>
                    </dl>
                  </details>
                  {selected.badge === "上下文" && (
                    <div className={styles.contextPanel}>
                      <p className={styles.contextPanelTitle}>会话上下文</p>
                      {contextState === "loading" && <p>加载会话上下文…</p>}
                      {contextState === "unavailable" && <p>暂无会话上下文快照</p>}
                      {contextState === "ready" && threadContext && (
                        <>
                          {threadContext.window && (
                            <p>
                              上下文窗口 {threadContext.window.total_tokens}/
                              {threadContext.window.max_tokens} tokens（
                              {Math.round(threadContext.window.percentage)}%，
                              {threadContext.window.model}）
                            </p>
                          )}
                          {(() => {
                            const digest = threadContext.digests?.at(-1);
                            if (!digest) return <p>暂无上下文摘要（首轮运行通常没有）。</p>;
                            const sections: ReadonlyArray<readonly [string, typeof digest.facts]> = [
                              ["事实", digest.facts],
                              ["决定", digest.decisions],
                              ["待办", digest.open_tasks],
                            ];
                            return sections.map(([label, entries]) =>
                              entries.length ? (
                                <div key={label} className={styles.contextSection}>
                                  <p>{label}（{entries.length}）</p>
                                  <ul>
                                    {entries.slice(0, 5).map((entry, index) => (
                                      <li key={index}>{entry.text}</li>
                                    ))}
                                  </ul>
                                </div>
                              ) : null,
                            );
                          })()}
                        </>
                      )}
                    </div>
                  )}
                </div>
              )}
              {activeTab === "prompt" && (
                <pre className={styles.code}>{selected.systemPrompt}</pre>
              )}
              {activeTab === "entries" && (
                <ul className={styles.entryList}>
                  {(selected.entries ?? []).map((entry) => (
                    <li key={entry.name}>
                      <strong>{entry.name}</strong>
                      {entry.description && <span>{entry.description}</span>}
                    </li>
                  ))}
                </ul>
              )}
              {activeTab === "arguments" && (
                <pre className={styles.code}>{selected.argumentsText}</pre>
              )}
              {activeTab === "output" && (
                <pre className={styles.code}>
                  {selected.output ?? selected.detail ?? "（无输出）"}
                </pre>
              )}
              {activeTab === "timing" && (
                <dl className={styles.timing}>
                  <div><dt>开始</dt><dd>{formatClock(selected.startMs)}</dd></div>
                  <div><dt>结束</dt><dd>{formatClock(selected.endMs)}</dd></div>
                  <div>
                    <dt>耗时</dt>
                    <dd>{formatDuration(selected.endMs - selected.startMs)}</dd>
                  </div>
                  <div><dt>轮次</dt><dd>第 {selected.turn} 轮</dd></div>
                </dl>
              )}
            </div>
          </aside>
        )}
      </div>

      <p className={styles.footnote}>
        轨迹来自本会话各轮次的服务端运行事件；模型级 Span 见开发者抽屉的外部 Trace 链接。
      </p>
    </div>
  );
}
