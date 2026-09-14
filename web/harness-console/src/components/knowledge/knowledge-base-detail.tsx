"use client";

import Link from "next/link";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type DragEvent,
} from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  studioClient,
  type StudioKnowledgeBase,
  type StudioKnowledgeDocumentChunk,
  type StudioKnowledgeDocumentStatus,
  type StudioKnowledgeDocumentTable,
} from "../../lib/studio-client";
import { KnowledgeGraphPanel } from "./knowledge-graph-panel";
import { KnowledgeWikiPanel } from "./knowledge-wiki-panel";
import { DrawerResizeHandle, useDrawerResize } from "../../lib/use-drawer-resize";
import { KnowledgeDrawerLayer } from "./knowledge-drawer-layer";
import styles from "./knowledge-base-detail.module.css";

type Tab = "docs" | "wiki" | "graph";

/** WeKnora returns RFC3339 timestamps; fall back to a dash when absent. */
function formatDocumentDate(value: string): string {
  if (!value) return "—";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "—";
  const pad = (input: number) => String(input).padStart(2, "0");
  return `${String(parsed.getFullYear()).slice(2)}-${pad(parsed.getMonth() + 1)}-${pad(
    parsed.getDate(),
  )} ${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`;
}

const PARSE_LABELS: Record<string, string> = {
  pending: "排队中",
  processing: "解析中",
  completed: "已完成",
  failed: "失败",
  draft: "草稿",
};

export function KnowledgeBaseDetail({ reference }: { reference: string }) {
  const [base, setBase] = useState<StudioKnowledgeBase | null>(null);
  const [tab, setTab] = useState<Tab>("docs");
  const [documents, setDocuments] = useState<StudioKnowledgeDocumentStatus[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [selected, setSelected] = useState<StudioKnowledgeDocumentStatus | null>(null);
  const [chunks, setChunks] = useState<StudioKnowledgeDocumentChunk[]>([]);
  const [chunksLoading, setChunksLoading] = useState(false);
  const [showManual, setShowManual] = useState(false);
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [creating, setCreating] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [graphFocus, setGraphFocus] = useState<string | null>(null);
  const [docQuery, setDocQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | "completed" | "processing" | "failed">(
    "all",
  );
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [pendingDeletes, setPendingDeletes] = useState<Set<string>>(new Set());
  const [bulkBusy, setBulkBusy] = useState(false);
  const [menuFor, setMenuFor] = useState<string | null>(null);
  const [contentView, setContentView] = useState<"full" | "chunks" | "table">("full");
  const [table, setTable] = useState<StudioKnowledgeDocumentTable | null>(null);
  const [tableLoading, setTableLoading] = useState(false);
  const [tableError, setTableError] = useState("");
  const [dragging, setDragging] = useState(false);
  const [summaryExpanded, setSummaryExpanded] = useState(false);
  const [batchMode, setBatchMode] = useState(false);
  const { width: drawerWidth, startResize: startDrawerResize } = useDrawerResize(
    "document",
    { min: 400, max: 1100 },
  );
  const fileRef = useRef<HTMLInputElement>(null);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const isWeknora = base?.engine === "weknora";
  const SPREADSHEET_TYPES = ["xls", "xlsx", "xlsm", "csv"];
  const isSpreadsheet = (fileType: string) =>
    SPREADSHEET_TYPES.includes((fileType || "").toLowerCase());

  const loadDocuments = useCallback(
    async (quiet = false) => {
      if (!quiet) setLoading(true);
      try {
        const docs = await studioClient.listKnowledgeDocuments(reference);
        setDocuments(docs);
        // WeKnora deletes documents in the background, so a just-deleted row
        // keeps coming back from the list for a few seconds. Hold it hidden
        // until the engine agrees it is gone.
        setPendingDeletes((current) => {
          if (current.size === 0) return current;
          const present = new Set(docs.map((doc) => doc.documentId));
          const next = new Set([...current].filter((documentId) => present.has(documentId)));
          return next.size === current.size ? current : next;
        });
        const active = docs.some(
          (doc) => doc.parseStatus === "processing" || doc.parseStatus === "pending",
        );
        if (active && pollTimer.current === null) {
          pollTimer.current = setInterval(() => void loadDocuments(true), 6000);
        } else if (!active && pollTimer.current !== null) {
          clearInterval(pollTimer.current);
          pollTimer.current = null;
        }
      } catch (cause) {
        if (!quiet) {
          setError(cause instanceof Error ? cause.message : "加载文档失败");
        }
      } finally {
        if (!quiet) setLoading(false);
      }
    },
    [reference],
  );

  const load = useCallback(async () => {
    setError("");
    try {
      const [baseValue, docs] = await Promise.all([
        studioClient.getKnowledgeBase(reference),
        studioClient.listKnowledgeDocuments(reference).catch(() => [] as StudioKnowledgeDocumentStatus[]),
      ]);
      setBase(baseValue);
      setDocuments(docs);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "加载知识库失败");
    } finally {
      setLoading(false);
    }
  }, [reference]);

  useEffect(() => {
    void load();
    return () => {
      if (pollTimer.current) clearInterval(pollTimer.current);
    };
  }, [load]);

  useEffect(() => {
    if (pendingDeletes.size === 0) return;
    const timer = setInterval(() => void loadDocuments(true), 2500);
    return () => clearInterval(timer);
  }, [loadDocuments, pendingDeletes]);

  // Dropping a file anywhere outside the drop zone would otherwise make the
  // browser open or download it instead of uploading.
  useEffect(() => {
    if (!isWeknora || tab !== "docs") return;
    const swallow = (event: Event) => event.preventDefault();
    window.addEventListener("dragover", swallow);
    window.addEventListener("drop", swallow);
    return () => {
      window.removeEventListener("dragover", swallow);
      window.removeEventListener("drop", swallow);
    };
  }, [isWeknora, tab]);

  const openDocument = useCallback(
    async (doc: StudioKnowledgeDocumentStatus) => {
      setSelected(doc);
      setSummaryExpanded(false);
      setTable(null);
      setTableError("");
      const spreadsheet = ["xls", "xlsx", "xlsm", "csv"].includes(
        (doc.fileType || "").toLowerCase(),
      );
      // Spreadsheet documents open straight into the parsed table view.
      setContentView(spreadsheet ? "table" : "full");
      setChunks([]);
      setChunksLoading(true);
      try {
        setChunks(await studioClient.listKnowledgeDocumentChunks(reference, doc.documentId));
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "加载切片失败");
      } finally {
        setChunksLoading(false);
      }
      if (spreadsheet) {
        setTableLoading(true);
        try {
          setTable(
            await studioClient.getKnowledgeDocumentTable(reference, doc.documentId),
          );
        } catch (cause) {
          setTableError(cause instanceof Error ? cause.message : "表格解析失败");
        } finally {
          setTableLoading(false);
        }
      }
    },
    [reference],
  );

  const closeDrawer = useCallback(() => {
    setSelected(null);
    setChunks([]);
    setTable(null);
    setTableError("");
  }, []);

  const showTable = useCallback(async () => {
    setContentView("table");
    if (table || !selected) return;
    setTableLoading(true);
    setTableError("");
    try {
      setTable(
        await studioClient.getKnowledgeDocumentTable(reference, selected.documentId),
      );
    } catch (cause) {
      setTableError(cause instanceof Error ? cause.message : "表格解析失败");
    } finally {
      setTableLoading(false);
    }
  }, [reference, selected, table]);

  const submitManual = useCallback(async () => {
    if (!title.trim() || !content.trim()) return;
    setCreating(true);
    setError("");
    try {
      await studioClient.createKnowledgeDocument(reference, {
        title: title.trim(),
        content: content.trim(),
      });
      setShowManual(false);
      setTitle("");
      setContent("");
      setNotice("手动文档已创建，正在解析");
      await loadDocuments();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "创建文档失败");
    } finally {
      setCreating(false);
    }
  }, [content, loadDocuments, reference, title]);

  const uploadFile = useCallback(
    async (file: File) => {
      setUploading(true);
      setError("");
      try {
        await studioClient.uploadKnowledgeDocument(reference, file);
        setNotice(`「${file.name}」已上传，正在解析`);
        await loadDocuments();
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "上传失败");
      } finally {
        setUploading(false);
      }
    },
    [loadDocuments, reference],
  );

  const onUpload = useCallback(async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    await uploadFile(file);
    if (fileRef.current) fileRef.current.value = "";
  }, [uploadFile]);

  const onDrop = useCallback(
    async (event: DragEvent<HTMLElement>) => {
      event.preventDefault();
      setDragging(false);
      if (!isWeknora || uploading) return;
      const files = Array.from(event.dataTransfer?.files ?? []);
      if (files.length === 0) return;
      for (const file of files) {
        await uploadFile(file);
      }
    },
    [isWeknora, uploadFile, uploading],
  );

  const onDelete = useCallback(
    async (doc: StudioKnowledgeDocumentStatus) => {
      if (!window.confirm(`删除文档「${doc.title}」？WeKnora 中的切片将一并删除。`)) return;
      setError("");
      try {
        await studioClient.deleteKnowledgeDocument(reference, doc.documentId);
        setSelected(null);
        setPendingDeletes((current) => new Set(current).add(doc.documentId));
        setNotice("文档已删除");
        await loadDocuments();
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "删除失败");
      }
    },
    [loadDocuments, reference],
  );

  const onReparse = useCallback(
    async (doc: StudioKnowledgeDocumentStatus) => {
      setError("");
      try {
        await studioClient.reparseKnowledgeDocument(reference, doc.documentId);
        setNotice("已触发重新解析");
        await loadDocuments();
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "重解析失败");
      }
    },
    [loadDocuments, reference],
  );

  const activeDocuments = useMemo(
    () => documents.filter((doc) => !pendingDeletes.has(doc.documentId)),
    [documents, pendingDeletes],
  );

  const visibleDocuments = useMemo(() => {
    const query = docQuery.trim().toLowerCase();
    return activeDocuments.filter((doc) => {
      if (query && !doc.title.toLowerCase().includes(query)) return false;
      if (statusFilter === "all") return true;
      if (statusFilter === "completed") return doc.parseStatus === "completed";
      if (statusFilter === "processing") {
        return doc.parseStatus === "processing" || doc.parseStatus === "pending";
      }
      return doc.parseStatus === "failed";
    });
  }, [activeDocuments, docQuery, statusFilter]);

  const toggleSelect = useCallback((documentId: string) => {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(documentId)) next.delete(documentId);
      else next.add(documentId);
      return next;
    });
  }, []);

  const clearSelection = useCallback(() => setSelectedIds(new Set()), []);

  const exitBatchMode = useCallback(() => {
    setBatchMode(false);
    setSelectedIds(new Set());
    setMenuFor(null);
  }, []);

  const bulkReparse = useCallback(async () => {
    if (selectedIds.size === 0) return;
    setBulkBusy(true);
    setError("");
    try {
      for (const documentId of selectedIds) {
        await studioClient.reparseKnowledgeDocument(reference, documentId);
      }
      setNotice(`已触发 ${selectedIds.size} 个文档重新解析`);
      clearSelection();
      await loadDocuments();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "批量重建失败");
    } finally {
      setBulkBusy(false);
    }
  }, [clearSelection, loadDocuments, reference, selectedIds]);

  const bulkDelete = useCallback(async () => {
    if (selectedIds.size === 0) return;
    if (!window.confirm(`删除选中的 ${selectedIds.size} 个文档？WeKnora 中的切片将一并删除。`)) {
      return;
    }
    setBulkBusy(true);
    setError("");
    try {
      const removed = [...selectedIds];
      for (const documentId of removed) {
        await studioClient.deleteKnowledgeDocument(reference, documentId);
      }
      setPendingDeletes((current) => {
        const next = new Set(current);
        for (const documentId of removed) next.add(documentId);
        return next;
      });
      setNotice(`已删除 ${removed.length} 个文档`);
      clearSelection();
      await loadDocuments();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "批量删除失败");
    } finally {
      setBulkBusy(false);
    }
  }, [clearSelection, loadDocuments, reference, selectedIds]);

  if (loading) {
    return (
      <section className={styles.content}>
        <p className={styles.empty}>加载中…</p>
      </section>
    );
  }

  if (!base) {
    return (
      <section className={styles.content}>
        <p className={styles.empty}>{error || "知识库不存在"}</p>
        <Link className={styles.ghost} href="/studio/knowledge">
          返回知识库列表
        </Link>
      </section>
    );
  }

  return (
    <section className={styles.content}>
        <nav className={styles.crumbs}>
          <Link href="/studio/knowledge">知识库</Link>
          <span>/</span>
          <span>{base.displayName}</span>
        </nav>

        <div className={styles.head}>
          <div>
            <h1>{base.displayName}</h1>
            <p className={styles.headHint}>
              上传或拖入文档，解析完成后即可检索与问答
            </p>
          </div>
          <div className={styles.actions}>
            {isWeknora ? (
              <>
                <button
                  type="button"
                  className={styles.primary}
                  onClick={() => fileRef.current?.click()}
                  disabled={uploading}
                >
                  {uploading ? "上传中…" : "上传文件"}
                </button>
                <input
                  ref={fileRef}
                  type="file"
                  hidden
                  onChange={() => void onUpload()}
                />
                <button
                  type="button"
                  className={styles.ghost}
                  onClick={() => setShowManual(true)}
                >
                  手动建文档
                </button>
              </>
            ) : null}
          </div>
        </div>

        <div className={styles.tabs}>
          <button
            type="button"
            className={`${styles.tab} ${tab === "docs" ? styles.tabActive : ""}`}
            aria-pressed={tab === "docs"}
            onClick={() => setTab("docs")}
          >
            文档
          </button>
          <button
            type="button"
            className={`${styles.tab} ${tab === "wiki" ? styles.tabActive : ""}`}
            aria-pressed={tab === "wiki"}
            onClick={() => setTab("wiki")}
            disabled={!isWeknora}
          >
            Wiki
          </button>
          <button
            type="button"
            className={`${styles.tab} ${tab === "graph" ? styles.tabActive : ""}`}
            aria-pressed={tab === "graph"}
            onClick={() => setTab("graph")}
            disabled={!isWeknora}
          >
            图谱
          </button>
        </div>

        {error ? <p className={styles.error}>{error}</p> : null}
        {notice ? <p className={styles.notice}>{notice}</p> : null}

        {tab === "docs" ? (
          <section
            className={`${styles.dropZone} ${dragging ? styles.dropZoneActive : ""}`}
            onDragOver={(event) => {
              if (!isWeknora) return;
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={(event) => {
              if (event.currentTarget.contains(event.relatedTarget as Node)) return;
              setDragging(false);
            }}
            onDrop={(event) => void onDrop(event)}
            data-dragging={dragging ? "true" : "false"}
          >
            <div className={styles.toolbar}>
              <input
                className={styles.toolbarSearch}
                value={docQuery}
                onChange={(event) => setDocQuery(event.target.value)}
                placeholder="搜索文档名称…"
              />
              <select
                className={styles.toolbarSelect}
                value={statusFilter}
                onChange={(event) =>
                  setStatusFilter(event.target.value as typeof statusFilter)
                }
              >
                <option value="all">全部状态</option>
                <option value="completed">已完成</option>
                <option value="processing">解析中</option>
                <option value="failed">失败</option>
              </select>
              <span className={styles.toolbarCount}>
                共 {visibleDocuments.length} / {activeDocuments.length} 个文档
              </span>
            </div>

            {activeDocuments.length === 0 ? (
              <p className={styles.empty}>
                还没有文档。上传文件或手动创建文档，WeKnora 将自动解析并切片。
              </p>
            ) : visibleDocuments.length === 0 ? (
              <p className={styles.empty}>没有符合筛选条件的文档。</p>
            ) : (
              <div className={styles.docGrid}>
                {visibleDocuments.map((doc) => {
                  const isSelected = selectedIds.has(doc.documentId);
                  return (
                    <article
                      key={doc.documentId}
                      className={`${styles.docCard} ${isSelected ? styles.docCardSelected : ""}`}
                      onClick={() => void openDocument(doc)}
                      role="button"
                      tabIndex={0}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault();
                          void openDocument(doc);
                        }
                      }}
                    >
                      <header className={styles.docCardHead}>
                        {batchMode ? (
                          <input
                            type="checkbox"
                            className={styles.docCheckbox}
                            checked={isSelected}
                            onClick={(event) => event.stopPropagation()}
                            onChange={() => toggleSelect(doc.documentId)}
                            aria-label={`选择 ${doc.title}`}
                          />
                        ) : null}
                        <h3 className={styles.docCardTitle} title={doc.title}>
                          {doc.title}
                        </h3>
                        <div className={styles.docCardMenuWrap}>
                          <button
                            type="button"
                            className={styles.docCardOpen}
                            aria-expanded={menuFor === doc.documentId}
                            aria-haspopup="menu"
                            title="更多操作"
                            onClick={(event) => {
                              event.stopPropagation();
                              setMenuFor((current) =>
                                current === doc.documentId ? null : doc.documentId,
                              );
                            }}
                          >
                            ⋯
                          </button>
                          {menuFor === doc.documentId ? (
                            <div
                              className={styles.docCardMenu}
                              role="menu"
                              onClick={(event) => event.stopPropagation()}
                            >
                              <button
                                type="button"
                                role="menuitem"
                                onClick={() => {
                                  setMenuFor(null);
                                  void openDocument(doc);
                                }}
                              >
                                查看
                              </button>
                              <button
                                type="button"
                                role="menuitem"
                                disabled={bulkBusy}
                                onClick={() => {
                                  setMenuFor(null);
                                  void onReparse(doc);
                                }}
                              >
                                重新解析
                              </button>
                              <button
                                type="button"
                                role="menuitem"
                                onClick={() => {
                                  setMenuFor(null);
                                  setBatchMode(true);
                                }}
                              >
                                批量管理
                              </button>
                              <button
                                type="button"
                                role="menuitem"
                                className={styles.docCardMenuDanger}
                                disabled={bulkBusy}
                                onClick={() => {
                                  setMenuFor(null);
                                  void onDelete(doc);
                                }}
                              >
                                删除
                              </button>
                            </div>
                          ) : null}
                        </div>
                      </header>
                      <p className={styles.docCardDesc}>
                        {doc.summaryStatus === "completed"
                          ? "已生成摘要，可查看切片与引用"
                          : "点击查看解析切片"}
                      </p>
                      <footer className={styles.docCardFoot}>
                        <span className={styles.docCardDate}>
                          {formatDocumentDate(doc.createdAt)}
                        </span>
                        <span className={styles.docCardTags}>
                          <span
                            className={`${styles.statusChip} ${
                              doc.parseStatus === "completed"
                                ? styles.statusCompleted
                                : doc.parseStatus === "processing" ||
                                    doc.parseStatus === "pending"
                                  ? styles.statusProcessing
                                  : styles.statusFailed
                            }`}
                          >
                            {PARSE_LABELS[doc.parseStatus] ?? doc.parseStatus}
                          </span>
                          <span className={styles.fileBadge}>
                            {(doc.fileType || "manual").toUpperCase()}
                          </span>
                        </span>
                      </footer>
                    </article>
                  );
                })}
              </div>
            )}

            {batchMode ? (
              <div className={styles.bulkBar}>
                <span className={styles.bulkCount}>已选 {selectedIds.size} 项</span>
                <button type="button" className={styles.bulkLink} onClick={clearSelection}>
                  取消选择
                </button>
                <button type="button" className={styles.bulkLink} onClick={exitBatchMode}>
                  退出批量
                </button>
                <span className={styles.bulkSpacer} />
                <button
                  type="button"
                  className={styles.ghost}
                  disabled={bulkBusy || selectedIds.size === 0}
                  onClick={() => void bulkReparse()}
                >
                  重建知识
                </button>
                <button
                  type="button"
                  className={styles.bulkDanger}
                  disabled={bulkBusy || selectedIds.size === 0}
                  onClick={() => void bulkDelete()}
                >
                  批量删除
                </button>
              </div>
            ) : null}

          </section>
        ) : tab === "wiki" ? (
          <KnowledgeWikiPanel
            reference={reference}
            onOpenGraph={(slug) => {
              setGraphFocus(slug);
              setTab("graph");
            }}
          />
        ) : (
          <KnowledgeGraphPanel reference={reference} focusSlug={graphFocus} />
        )}

        {showManual ? (
          <KnowledgeDrawerLayer onClose={() => setShowManual(false)}>
          <div
            className={styles.overlay}
            role="presentation"
            onClick={(event) => {
              if (event.target === event.currentTarget) setShowManual(false);
            }}
          >
            <div className={styles.dialog} role="dialog" aria-modal="true" aria-label="手动创建文档">
              <h2>手动创建文档</h2>
              <div className={styles.field}>
                <label htmlFor="manual-title">标题</label>
                <input
                  id="manual-title"
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  placeholder="例如：非法集资认定要点"
                />
              </div>
              <div className={styles.field}>
                <label htmlFor="manual-content">内容（Markdown 或纯文本）</label>
                <textarea
                  id="manual-content"
                  value={content}
                  onChange={(event) => setContent(event.target.value)}
                  placeholder="粘贴正文内容，WeKnora 将自动解析、切片并构建向量"
                />
              </div>
              <div className={styles.dialogActions}>
                <button
                  type="button"
                  className={styles.ghost}
                  onClick={() => setShowManual(false)}
                >
                  取消
                </button>
                <button
                  type="button"
                  className={styles.primary}
                  disabled={creating || !title.trim() || !content.trim()}
                  onClick={() => void submitManual()}
                >
                  {creating ? "创建中…" : "创建"}
                </button>
              </div>
            </div>
          </div>
          </KnowledgeDrawerLayer>
        ) : null}

        {selected ? (
          <KnowledgeDrawerLayer onClose={closeDrawer}>
            <div className={styles.drawerOverlay} onClick={closeDrawer} role="presentation" />
            <aside
              className={styles.drawer}
              role="dialog"
              aria-modal="true"
              aria-label="文档详情"
              style={{ width: drawerWidth }}
            >
              <DrawerResizeHandle
                onPointerDown={startDrawerResize}
                className={styles.resizeHandle}
              />
              <header className={styles.drawerHead}>
                <span className={styles.drawerGlyph} aria-hidden="true">
                  <svg viewBox="0 0 20 20">
                    <path d="M4.5 3.5h7l4 4v9h-11z" />
                    <path d="M11.5 3.5v4h4M7.5 11h5m-5 2.8h5" />
                  </svg>
                </span>
                <h2>{selected.title}</h2>
                <button
                  type="button"
                  className={styles.drawerClose}
                  onClick={closeDrawer}
                  aria-label="关闭"
                  title="关闭"
                >
                  ×
                </button>
              </header>

              <section className={styles.drawerSection}>
                <h3 className={styles.drawerSectionTitle}>基本信息</h3>
                <dl className={styles.drawerFacts}>
                  <dt>创建时间</dt>
                  <dd>{formatDocumentDate(selected.createdAt)}</dd>
                  <dt>类型</dt>
                  <dd>
                    <span className={styles.drawerTypeBadge}>
                      {selected.fileType ? selected.fileType.toUpperCase() : "手动创建"}
                    </span>
                  </dd>
                  <dt>解析状态</dt>
                  <dd>
                    <span
                      className={`${styles.statusChip} ${
                        selected.parseStatus === "completed"
                          ? styles.statusCompleted
                          : selected.parseStatus === "processing" ||
                              selected.parseStatus === "pending"
                            ? styles.statusProcessing
                            : styles.statusFailed
                      }`}
                    >
                      {PARSE_LABELS[selected.parseStatus] ?? selected.parseStatus}
                    </span>
                  </dd>
                </dl>
              </section>

              {selected.description ? (
                <section className={styles.drawerSection}>
                  <h3 className={styles.drawerSectionTitle}>摘要</h3>
                  <div className={styles.summaryBox}>
                    <p
                      className={`${styles.drawerSummary} ${
                        summaryExpanded ? styles.drawerSummaryOpen : ""
                      }`}
                    >
                      {selected.description}
                    </p>
                    <button
                      type="button"
                      className={styles.summaryToggle}
                      aria-expanded={summaryExpanded}
                      aria-label={summaryExpanded ? "收起摘要" : "展开摘要"}
                      onClick={() => setSummaryExpanded((current) => !current)}
                    >
                      <svg viewBox="0 0 16 16" aria-hidden="true">
                        <path d="m4.5 6.5 3.5 3.5 3.5-3.5" />
                      </svg>
                    </button>
                  </div>
                </section>
              ) : null}

              <section className={styles.drawerSection}>
                <div className={styles.drawerSectionHead}>
                  <h3 className={styles.drawerSectionTitle}>文档内容</h3>
                  <span className={styles.drawerCount}>共 {chunks.length} 个片段</span>
                  <div className={styles.drawerToggle}>
                    <button
                      type="button"
                      className={contentView === "full" ? styles.drawerToggleActive : ""}
                      onClick={() => setContentView("full")}
                    >
                      全文
                    </button>
                    {isSpreadsheet(selected.fileType) ? (
                      <button
                        type="button"
                        className={contentView === "table" ? styles.drawerToggleActive : ""}
                        onClick={() => void showTable()}
                      >
                        表格
                      </button>
                    ) : null}
                    <button
                      type="button"
                      className={contentView === "chunks" ? styles.drawerToggleActive : ""}
                      onClick={() => setContentView("chunks")}
                    >
                      查看分块
                    </button>
                  </div>
                </div>
                {contentView === "table" ? (
                  tableLoading ? (
                    <p className={styles.empty}>表格解析中…</p>
                  ) : tableError ? (
                    <p className={styles.error}>{tableError}</p>
                  ) : table ? (
                    <div className={styles.tableWrap}>
                      <table className={styles.docTable}>
                        <tbody>
                          {table.rows.map((row, rowIndex) => (
                            <tr key={rowIndex}>
                              {row.map((cell, cellIndex) => (
                                <td key={cellIndex}>{cell}</td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {table.truncated ? (
                        <p className={styles.tableNote}>表格过大，仅展示前若干行/列</p>
                      ) : null}
                    </div>
                  ) : null
                ) : chunksLoading ? (
                  <p className={styles.empty}>切片加载中…</p>
                ) : chunks.length === 0 ? (
                  <p className={styles.empty}>解析完成后这里会显示文档内容</p>
                ) : contentView === "full" ? (
                  <div className={styles.drawerFullText}>
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                      {chunks
                        .slice()
                        .sort((left, right) => left.seq - right.seq)
                        .map((chunk) => chunk.content)
                        .join("\n\n")}
                    </ReactMarkdown>
                  </div>
                ) : (
                  <div className={styles.chunkList}>
                    {chunks.map((chunk) => (
                      <article key={chunk.chunkId} className={styles.chunkCard}>
                        <p className={styles.chunkIndex}>片段 {chunk.seq}</p>
                        <div className={styles.chunkContent}>
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>
                            {chunk.content}
                          </ReactMarkdown>
                        </div>
                      </article>
                    ))}
                  </div>
                )}
              </section>
            </aside>
          </KnowledgeDrawerLayer>
        ) : null}
    </section>
  );
}
