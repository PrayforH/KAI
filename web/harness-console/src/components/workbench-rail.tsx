"use client";

import { useEffect, useState } from "react";
import { requireAuthenticatedResponse } from "../lib/client-auth";
import { PanelResizeHandle } from "./panel-resize-handle";
import { SidebarPanelIcon } from "./panel-icons";
import { RailFilePreview, previewKindFor, type PreviewKind, type PreviewTarget } from "./rail-file-preview";


const PHASE_LABELS: Record<string, string> = {
  idle: "空闲",
  queued: "排队中",
  running: "运行中",
  waiting_approval: "待审批",
  cancelling: "取消中",
  cancelled: "已取消",
  succeeded: "已完成",
  completed: "已完成",
  failed: "失败",
  rejected: "已拒绝",
  timed_out: "已超时",
  unknown: "未知",
};

const SCOPE_LABELS: Record<string, string> = {
  personal: "个人",
  team: "团队",
  historical: "历史",
  restored: "本地",
};

function RefreshIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M15.8 8.4A6 6 0 1 0 16 11.7" />
      <path d="M15.9 4.6v3.9h-3.9" />
    </svg>
  );
}

function SearchIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="9" cy="9" r="5.2" />
      <path d="m13 13 3.4 3.4" />
    </svg>
  );
}

function FilesIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M4.4 4.6h11.2M4.4 8h11.2M4.4 11.4h11.2M4.4 14.8h11.2" />
    </svg>
  );
}

function ExpandIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M11.6 4.4h4v4" />
      <path d="M15.6 4.4 9.8 10.2" />
      <path d="M8.4 15.6h-4v-4" />
      <path d="M4.4 15.6 10.2 9.8" />
    </svg>
  );
}

function ChevronIcon({ direction }: { direction: "back" | "forward" }) {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.7"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={direction === "back" ? "M12.2 5.4 7.6 10l4.6 4.6" : "M7.8 5.4 12.4 10l-4.6 4.6"} />
    </svg>
  );
}

interface RailFile {
  artifact_id: string;
  name: string;
  media_type: string;
  size_bytes?: number | null;
  thread_id?: string;
}

/** Grok-style row glyph: one small icon carries the file type. */
export function railFileIcon(kind: PreviewKind) {
  const paths =
    kind === "image" ? (
      <>
        <rect x="2.2" y="3.2" width="11.6" height="9.6" rx="1.6" />
        <circle cx="6.1" cy="6.8" r="1.15" />
        <path d="M2.8 11.4 6.6 8l3.4 3.4 1.7-1.5 2.1 1.9" />
      </>
    ) : kind === "csv" ? (
      <>
        <rect x="2.2" y="3.2" width="11.6" height="9.6" rx="1.6" />
        <path d="M2.2 6.6h11.6M6.2 3.2v9.6M10.2 3.2v9.6" />
      </>
    ) : kind === "html" ? (
      <>
        <rect x="2.2" y="3.2" width="11.6" height="9.6" rx="1.6" />
        <path d="M2.2 6.2h11.6" />
        <path d="M4.2 4.7h.01M6 4.7h.01" />
      </>
    ) : kind === "code" || kind === "json" ? (
      <>
        <path d="M6.2 5.6 3.4 8l2.8 2.4M9.8 5.6 12.6 8l-2.8 2.4" />
      </>
    ) : (
      <>
        <path d="M4.2 2.6h5l3 3v7.8H4.2z" />
        <path d="M9.2 2.6v3.2h3" />
        <path d="M6.2 8.4h4M6.2 10.6h4" />
      </>
    );
  return (
    <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.3"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {paths}
    </svg>
  );
}

function formatFileSize(value?: number | null) {
  if (value === null || value === undefined) return "";
  if (value < 1_024) return `${value} B`;
  if (value < 1_048_576) return `${(value / 1_024).toFixed(value < 10_240 ? 1 : 0)} KB`;
  return `${(value / 1_048_576).toFixed(1)} MB`;
}

export function WorkbenchRail({
  open,
  onClose,
  expanded,
  onToggleExpanded,
  taskTitle,
  agentDisplay,
  agentKey,
  agentScope,
  modelRoute,
  runPhase,
  threadId,
  previewRequest,
}: {
  open: boolean;
  onClose: () => void;
  expanded: boolean;
  onToggleExpanded: () => void;
  taskTitle: string;
  agentDisplay: string;
  agentKey: string;
  agentScope: string;
  modelRoute: string | null;
  runPhase: string | null;
  threadId: string;
  previewRequest?: (PreviewTarget & { nonce: number }) | null;
}) {
  const [tab, setTab] = useState<"files" | "details">("files");
  const [query, setQuery] = useState("");
  const [searchOpen, setSearchOpen] = useState(false);
  const [listVisible, setListVisible] = useState(true);
  const [fileError, setFileError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [loadedFiles, setFiles] = useState<RailFile[]>([]);
  // Scope again at render time: effects run after task-switch renders.
  const files = loadedFiles.filter((file) => file.thread_id === threadId);
  const [filesLoading, setFilesLoading] = useState(true);
  // The list is the first entry, so back always finds its way out of a preview.
  const [trail, setTrail] = useState<{ items: (PreviewTarget | null)[]; index: number }>({
    items: [null],
    index: 0,
  });
  const selected = trail.items[trail.index] ?? null;
  const show = (target: PreviewTarget | null) => setTrail((current) => ({
    items: [...current.items.slice(0, current.index + 1), target],
    index: current.index + 1,
  }));
  const step = (delta: number) => setTrail((current) => ({
    ...current,
    index: Math.min(current.items.length - 1, Math.max(0, current.index + delta)),
  }));

  // The transcript's artifact cards open their file here instead of a new tab.
  useEffect(() => {
    if (!previewRequest) return;
    setTab("files");
    show(previewRequest);
  }, [previewRequest]);

  // A task switch shows that task's files, never the previous selection.
  useEffect(() => {
    setTrail({ items: [null], index: 0 });
  }, [threadId]);

  useEffect(() => {
    if (!open) return;
    if (!threadId) {
      setFiles([]);
      setFilesLoading(false);
      return;
    }
    const controller = new AbortController();
    setFilesLoading(true);
    setFiles([]);
    setFileError("");
    async function load() {
      try {
        const response = requireAuthenticatedResponse(
          await fetch(`/api/harness/artifacts?thread_id=${encodeURIComponent(threadId)}&limit=500`, {
            cache: "no-store",
            signal: controller.signal,
          }),
        );
        if (!response.ok) throw new Error();
        const all = (await response.json()) as RailFile[];
        if (!controller.signal.aborted) setFiles(all.filter((file) => file.thread_id === threadId));
      } catch {
        if (!controller.signal.aborted) setFileError("文件暂时无法读取，请重试。");
      } finally {
        if (!controller.signal.aborted) setFilesLoading(false);
      }
    }
    void load();
    return () => controller.abort();
  }, [threadId, runPhase, open, refresh]);

  const phaseKnown = runPhase && runPhase in PHASE_LABELS;
  const phaseKey = (phaseKnown ? runPhase : "unknown") as
    | keyof typeof PHASE_LABELS
    | "unknown";
  const scopeLabel = SCOPE_LABELS[agentScope] ?? agentScope;

  const fileList = (
    <>
      {fileError ? <p role="alert">{fileError} <button type="button" onClick={() => setRefresh((value) => value + 1)}>重试</button></p> : filesLoading ? (
        <span className="workbench-rail-files-empty">正在读取文件…</span>
      ) : files.length === 0 ? (
        <span className="workbench-rail-files-empty">生成的文档、图片与其他成果会保存在这里。</span>
      ) : (
        <div className="workbench-rail-files">
          {!files.some((file) => file.name.toLowerCase().includes(query.toLowerCase())) && <p>没有匹配的文件</p>}
          {files.filter((file) => file.name.toLowerCase().includes(query.toLowerCase())).map((file) => {
            const kind = previewKindFor(file.media_type ?? "", file.name ?? "");
            const previewable = kind !== "none";
            const target: PreviewTarget = {
              artifact_id: file.artifact_id,
              name: file.name,
              media_type: file.media_type,
              size_bytes: file.size_bytes,
              thread_id: file.thread_id ?? threadId,
            };
            return (
              <div className="rail-file-row" key={file.artifact_id}>
                <button
                  type="button"
                  className="workbench-rail-file"
                  aria-pressed={selected?.artifact_id === file.artifact_id}
                  onClick={() => show(target)}
                  title={previewable ? `在侧栏预览 ${file.name}` : `查看 ${file.name}`}
                >
                  <span className="rail-file-icon" data-kind={kind}>{railFileIcon(kind)}</span>
                  <span className="workbench-rail-file-name">{file.name}</span>
                  <span className="workbench-rail-file-size">{formatFileSize(file.size_bytes)}</span>
                </button>
                <a className="rail-download" href={`/api/harness/artifacts/${encodeURIComponent(file.artifact_id)}?thread_id=${encodeURIComponent(threadId)}`} download={file.name} title={`下载 ${file.name}`} aria-label={`下载 ${file.name}`}>↓</a>
              </div>
            );
          })}
        </div>
      )}
    </>
  );

  return (
    <aside
      className="workbench-rail"
      aria-hidden={open ? undefined : true}
      aria-label="任务工作区"
      inert={!open}
      tabIndex={-1}
    >
      {open && <PanelResizeHandle panel="rail" />}
      <div className="workbench-rail-panel" aria-hidden={!open}>
        <header className="rail-bar">
          <nav className="rail-tabs" aria-label="工作区视图">
            <button type="button" aria-pressed={tab === "files"} onClick={() => setTab("files")}>文件</button>
            <button type="button" aria-pressed={tab === "details"} onClick={() => setTab("details")}>任务详情</button>
          </nav>
          <div className="rail-bar-actions">
            <button
              type="button"
              className="rail-bar-button"
              aria-label={expanded ? "还原对话区" : "扩展占满对话区"}
              title={expanded ? "还原对话区" : "扩展占满对话区"}
              aria-pressed={expanded}
              onClick={onToggleExpanded}
            >
              <ExpandIcon />
            </button>
            <button
              type="button"
              className="rail-bar-button"
              aria-label="收起任务上下文"
              title="收起任务上下文"
              onClick={onClose}
            >
              <SidebarPanelIcon />
            </button>
          </div>
        </header>
        <div className="rail-toolbar" hidden={tab !== "files"}>
          <button
            type="button"
            className="rail-bar-button"
            aria-label={listVisible ? "隐藏文件列表" : "显示文件列表"}
            title={listVisible ? "隐藏文件列表" : "显示文件列表"}
            aria-pressed={listVisible}
            onClick={() => setListVisible((value) => !value)}
          >
            <FilesIcon />
          </button>
          <button
            type="button"
            className="rail-bar-button"
            aria-label="搜索任务文件"
            title="搜索任务文件"
            aria-pressed={searchOpen}
            onClick={() => setSearchOpen((value) => !value)}
          >
            <SearchIcon />
          </button>
          <button
            type="button"
            className="rail-bar-button"
            aria-label="后退"
            title="后退"
            disabled={trail.index === 0}
            onClick={() => step(-1)}
          >
            <ChevronIcon direction="back" />
          </button>
          <button
            type="button"
            className="rail-bar-button"
            aria-label="前进"
            title="前进"
            disabled={trail.index >= trail.items.length - 1}
            onClick={() => step(1)}
          >
            <ChevronIcon direction="forward" />
          </button>
          <span className="rail-bar-divider" aria-hidden="true" />
          <button
            type="button"
            className="rail-bar-button"
            aria-label="刷新文件"
            title="刷新文件"
            onClick={() => setRefresh((value) => value + 1)}
          >
            <RefreshIcon />
          </button>
        </div>
        {searchOpen ? (
          <div className="rail-search-row">
            <input
              className="rail-file-search"
              type="search"
              aria-label="搜索任务文件"
              placeholder="搜索文件…"
              autoFocus
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key !== "Escape") return;
                setQuery("");
                setSearchOpen(false);
              }}
            />
          </div>
        ) : null}
        <div className="workbench-rail-body">
          <section
            className="workbench-rail-section rail-files-section"
            data-previewing={selected ? "true" : undefined}
            data-list-hidden={selected && !listVisible ? "true" : undefined}
            hidden={tab !== "files"}
          >
            {selected ? (
              // A wide drawer shows the browser and the file side by side; a
              // narrow one keeps the preview full width and hides the browser.
              <>
                <div className="rail-file-column">{fileList}</div>
                <div className="rail-preview-column"><RailFilePreview target={selected} /></div>
              </>
            ) : fileList}
          </section>
          <div hidden={tab !== "details"}>
          <section className="workbench-rail-section">
            <small>当前任务</small>
            <strong className="workbench-rail-task">{taskTitle}</strong>
          </section>
          <section className="workbench-rail-section">
            <small>智能体</small>
            <div className="workbench-rail-rows">
              <span className="workbench-rail-agent">{agentDisplay}</span>
              <code>{agentKey}</code>
            </div>
          </section>
          <section className="workbench-rail-section">
            <small>归属</small>
            <span className="workbench-rail-chip">{scopeLabel}</span>
          </section>
          <section className="workbench-rail-section">
            <small>模型路由</small>
            <span className="workbench-rail-rows">
              <code>{modelRoute ?? "默认（跟随智能体）"}</code>
            </span>
          </section>
          <section className="workbench-rail-section">
            <small>运行阶段</small>
            <span className={`workbench-rail-phase is-${phaseKey}`}>
              <i aria-hidden="true" />
              {PHASE_LABELS[phaseKey] ?? phaseKey}
            </span>
          </section>
          <section className="workbench-rail-section">
            <small>会话 ID</small>
            <code className="workbench-rail-thread">{threadId}</code>
          </section>
          </div>
        </div>
      </div>
    </aside>
  );
}
