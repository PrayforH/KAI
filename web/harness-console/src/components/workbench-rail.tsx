"use client";

import { useEffect, useState, type ReactNode } from "react";
import { groupWorkspaceFiles } from "../lib/workspace-file-groups";
import { requireAuthenticatedResponse } from "../lib/client-auth";
import { PanelResizeHandle } from "./panel-resize-handle";
import { SidebarPanelIcon } from "./panel-icons";
import { RailFilePreview, previewKindFor, type PreviewKind, type PreviewTarget } from "./rail-file-preview";


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

/** The browser column toggle in the file toolbar. */
function FilesIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M4.4 4.6h11.2M4.4 8h11.2M4.4 11.4h11.2M4.4 14.8h11.2" />
    </svg>
  );
}

/** The drawer's own view: the task's files, told apart from the list toggle. */
function FolderIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3.1 6.2a1.8 1.8 0 0 1 1.8-1.8h2.7l1.5 1.8h6a1.8 1.8 0 0 1 1.8 1.8v6.1a1.8 1.8 0 0 1-1.8 1.8H4.9a1.8 1.8 0 0 1-1.8-1.8z" />
    </svg>
  );
}

/** Observability: one run's trace, drawn as a scope sweep. */
function TraceIcon() {
  return (
    <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M2.8 12.6h2.6l2-5.4 2.6 8 2.2-5.4 1.4 2.8h3.6" />
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

export interface RailFile {
  artifact_id: string;
  name: string;
  media_type: string;
  size_bytes?: number | null;
  thread_id?: string;
  change?: "已修改" | "已新增" | "已删除";
  downloadHref?: string;
  group?: string;
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
  threadId,
  previewRequest,
  observabilityHref,
  runPhase,
  workspace,
  expandIcon,
}: {
  expandIcon?: ReactNode;
  workspace?: { files: RailFile[]; loading: boolean; error: string; onRefresh?: () => void; renderPreview: (file: RailFile) => ReactNode; note?: string };
  open: boolean;
  onClose: () => void;
  expanded: boolean;
  onToggleExpanded: () => void;
  threadId: string;
  previewRequest?: (PreviewTarget & { nonce: number }) | null;
  /** Langfuse trace for the current run, or null while no run exists. */
  observabilityHref: string | null;
  /** A phase change means the run may have written new artifacts. */
  runPhase: string | null;
}) {
  const [query, setQuery] = useState("");
  const [expandedGroups, setExpandedGroups] = useState<string[]>(["最终产出"]);
  const [searchOpen, setSearchOpen] = useState(false);
  const [listVisible, setListVisible] = useState(true);
  const [loadedError, setFileError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [loadedFiles, setFiles] = useState<RailFile[]>([]);
  // Scope again at render time: effects run after task-switch renders.
  const files = workspace?.files ?? loadedFiles.filter((file) => file.thread_id === threadId);
  const fileError = workspace?.error ?? loadedError;
  const [loadedLoading, setFilesLoading] = useState(true);
  const filesLoading = workspace?.loading ?? loadedLoading;
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
    show(previewRequest);
  }, [previewRequest]);

  // A task switch shows that task's files, never the previous selection.
  useEffect(() => {
    setTrail({ items: [null], index: 0 });
    setExpandedGroups(["最终产出"]);
  }, [threadId]);

  useEffect(() => {
    if (!open || workspace) return;
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
  }, [threadId, runPhase, open, refresh, Boolean(workspace)]);

  const refreshFiles = () => workspace ? workspace.onRefresh?.() : setRefresh(value => value + 1);
  const selectedFile = files.find(file => file.artifact_id === selected?.artifact_id);
  const matchingFiles = files.filter(file => file.name.toLowerCase().includes(query.trim().toLowerCase()));
  const fileGroups = groupWorkspaceFiles(files);
  function groupDisclosure(key: string, label: string, count: number, content: () => ReactNode) {
    const opened = expandedGroups.includes(key);
    return <details className="rail-file-group" key={key} open={opened} onToggle={event => {
      const next = event.currentTarget.open;
      setExpandedGroups(current => next ? current.includes(key) ? current : [...current, key] : current.filter(value => value !== key));
    }}>
      <summary><span>{label}</span><small>{count}</small></summary>
      {opened && content()}
    </details>;
  }
  function renderFile(file: RailFile) {
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
          <span className="workbench-rail-file-name">{query.trim() ? file.name : file.name.split("/").at(-1)}</span>
          <span className="workbench-rail-file-size" data-change={file.change}>{file.change || formatFileSize(file.size_bytes)}</span>
        </button>
        {(!workspace || file.downloadHref) && <a className="rail-download" href={file.downloadHref ?? `/api/harness/artifacts/${encodeURIComponent(file.artifact_id)}?thread_id=${encodeURIComponent(threadId)}`} download={file.name} title={`下载 ${file.name}`} aria-label={`下载 ${file.name}`}>↓</a>}
      </div>
    );

  }
  const fileList = (
    <>
      {workspace?.note && <p className="workbench-rail-files-empty">{workspace.note}</p>}
      {searchOpen ? (
        // The field belongs to the browser, so it takes the browser's width.
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
      ) : null}
      {fileError ? <p role="alert">{fileError} <button type="button" onClick={refreshFiles}>重试</button></p> : filesLoading ? (
        <span className="workbench-rail-files-empty">正在读取文件…</span>
      ) : files.length === 0 ? (
        <span className="workbench-rail-files-empty">生成的文档、图片与其他成果会保存在这里。</span>
      ) : (
        <div className="workbench-rail-files">
          {!matchingFiles.length && <p>没有匹配的文件</p>}
          {query.trim() ? matchingFiles.map(renderFile) : fileGroups.map(group => groupDisclosure(
            group.name, group.name, [...group.folders.values()].reduce((sum, entries) => sum + entries.length, 0),
            () => [...group.folders].map(([folder, entries]) => folder
              ? groupDisclosure(`${group.name}/${folder}`, folder, entries.length, () => entries.map(renderFile))
              : entries.map(renderFile)),
          ))}

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
          <div className="rail-bar-views">
            <button
              type="button"
              className="rail-bar-button"
              aria-label="文件列表"
              title="文件列表"
              aria-current={selected ? undefined : "page"}
              onClick={() => show(null)}
            >
              <FolderIcon />
            </button>
            {!workspace && (observabilityHref ? (
              <a
                className="rail-bar-button"
                aria-label="在 Langfuse 查看本次运行的 Trace"
                title="在 Langfuse 查看本次运行的 Trace"
                href={observabilityHref}
                target="_blank"
                rel="noreferrer"
              >
                <TraceIcon />
              </a>
            ) : (
              <span
                className="rail-bar-button is-disabled"
                aria-label="本次运行的 Trace 尚未生成"
                title="运行开始后可查看观测"
              >
                <TraceIcon />
              </span>
            ))}
          </div>
          <div className="rail-bar-actions">
            <button
              type="button"
              className="rail-bar-button"
              aria-label={expanded ? "还原对话区" : "扩展占满对话区"}
              title={expanded ? "还原对话区" : "扩展占满对话区"}
              aria-pressed={expanded}
              onClick={onToggleExpanded}
            >
              {expandIcon ?? <ExpandIcon />}
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
        <div className="rail-toolbar">
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
          {(!workspace || workspace.onRefresh) && <button
            type="button"
            className="rail-bar-button"
            aria-label="刷新文件"
            title="刷新文件"
            onClick={refreshFiles}
          >
            <RefreshIcon />
          </button>}
        </div>
        <div className="workbench-rail-body">
          <section
            className="workbench-rail-section rail-files-section"
            data-previewing={selected ? "true" : undefined}
            data-list-hidden={selected && !listVisible ? "true" : undefined}
          >
            {selected ? (
              // A wide drawer shows the browser and the file side by side; a
              // narrow one keeps the preview full width and hides the browser.
              <>
                <div className="rail-file-column">{fileList}</div>
                <div className="rail-preview-column">{workspace && selectedFile ? workspace.renderPreview(selectedFile) : <RailFilePreview target={selected} />}</div>
              </>
            ) : fileList}
          </section>
        </div>
      </div>
    </aside>
  );
}
