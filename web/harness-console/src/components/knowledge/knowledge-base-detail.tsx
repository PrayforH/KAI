"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  studioClient,
  type StudioKnowledgeBase,
  type StudioKnowledgeDocumentChunk,
  type StudioKnowledgeDocumentStatus,
} from "../../lib/studio-client";
import { KnowledgeGraphPanel } from "./knowledge-graph-panel";
import { KnowledgeWikiPanel } from "./knowledge-wiki-panel";
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
  const [bulkBusy, setBulkBusy] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null);

  const isWeknora = base?.engine === "weknora";

  const loadDocuments = useCallback(
    async (quiet = false) => {
      if (!quiet) setLoading(true);
      try {
        const docs = await studioClient.listKnowledgeDocuments(reference);
        setDocuments(docs);
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

  const openDocument = useCallback(
    async (doc: StudioKnowledgeDocumentStatus) => {
      setSelected(doc);
      setChunks([]);
      setChunksLoading(true);
      try {
        setChunks(await studioClient.listKnowledgeDocumentChunks(reference, doc.documentId));
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "加载切片失败");
      } finally {
        setChunksLoading(false);
      }
    },
    [reference],
  );

  const closeDrawer = useCallback(() => {
    setSelected(null);
    setChunks([]);
  }, []);

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

  const onUpload = useCallback(async () => {
    const file = fileRef.current?.files?.[0];
    if (!file) return;
    setUploading(true);
    setError("");
    try {
      await studioClient.uploadKnowledgeDocument(reference, file);
      setNotice(`「${file.name}」已上传，正在解析`);
      if (fileRef.current) fileRef.current.value = "";
      await loadDocuments();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "上传失败");
    } finally {
      setUploading(false);
    }
  }, [loadDocuments, reference]);

  const onDelete = useCallback(
    async (doc: StudioKnowledgeDocumentStatus) => {
      if (!window.confirm(`删除文档「${doc.title}」？WeKnora 中的切片将一并删除。`)) return;
      setError("");
      try {
        await studioClient.deleteKnowledgeDocument(reference, doc.documentId);
        setSelected(null);
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

  const visibleDocuments = useMemo(() => {
    const query = docQuery.trim().toLowerCase();
    return documents.filter((doc) => {
      if (query && !doc.title.toLowerCase().includes(query)) return false;
      if (statusFilter === "all") return true;
      if (statusFilter === "completed") return doc.parseStatus === "completed";
      if (statusFilter === "processing") {
        return doc.parseStatus === "processing" || doc.parseStatus === "pending";
      }
      return doc.parseStatus === "failed";
    });
  }, [documents, docQuery, statusFilter]);

  const toggleSelect = useCallback((documentId: string) => {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(documentId)) next.delete(documentId);
      else next.add(documentId);
      return next;
    });
  }, []);

  const clearSelection = useCallback(() => setSelectedIds(new Set()), []);

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
      for (const documentId of selectedIds) {
        await studioClient.deleteKnowledgeDocument(reference, documentId);
      }
      setNotice(`已删除 ${selectedIds.size} 个文档`);
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
              支持点击或拖拽上传，多格式文档自动解析并智能分块，快速构建可检索的知识库
            </p>
          </div>
          <div className={styles.actions}>
            {isWeknora ? (
              <>
                <button
                  type="button"
                  className={styles.ghost}
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
                  className={styles.primary}
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
            onClick={() => setTab("docs")}
          >
            文档
          </button>
          <button
            type="button"
            className={`${styles.tab} ${tab === "wiki" ? styles.tabActive : ""}`}
            onClick={() => setTab("wiki")}
            disabled={!isWeknora}
          >
            Wiki
          </button>
          <button
            type="button"
            className={`${styles.tab} ${tab === "graph" ? styles.tabActive : ""}`}
            onClick={() => setTab("graph")}
            disabled={!isWeknora}
          >
            图谱
          </button>
        </div>

        {error ? <p className={styles.error}>{error}</p> : null}
        {notice ? <p className={styles.notice}>{notice}</p> : null}

        {tab === "docs" ? (
          <section>
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
                共 {visibleDocuments.length} / {documents.length} 个文档
              </span>
            </div>

            {documents.length === 0 ? (
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
                    >
                      <header className={styles.docCardHead}>
                        <input
                          type="checkbox"
                          className={styles.docCheckbox}
                          checked={isSelected}
                          onChange={() => toggleSelect(doc.documentId)}
                          aria-label={`选择 ${doc.title}`}
                        />
                        <h3 className={styles.docCardTitle} title={doc.title}>
                          {doc.title}
                        </h3>
                        <button
                          type="button"
                          className={styles.docCardOpen}
                          onClick={() => void openDocument(doc)}
                          title="查看切片"
                        >
                          ⋯
                        </button>
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

            {selectedIds.size > 0 ? (
              <div className={styles.bulkBar}>
                <span className={styles.bulkCount}>已选 {selectedIds.size} 项</span>
                <button type="button" className={styles.bulkLink} onClick={clearSelection}>
                  取消选择
                </button>
                <span className={styles.bulkSpacer} />
                <button
                  type="button"
                  className={styles.ghost}
                  disabled={bulkBusy}
                  onClick={() => void bulkReparse()}
                >
                  重建知识
                </button>
                <button
                  type="button"
                  className={styles.bulkDanger}
                  disabled={bulkBusy}
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
        ) : null}

        {selected ? (
          <>
            <div className={styles.drawerOverlay} onClick={closeDrawer} role="presentation" />
            <aside className={styles.drawer} aria-label="文档详情">
              <div className={styles.drawerHead} style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
                <h2>{selected.title}</h2>
                <button type="button" className={styles.ghost} onClick={closeDrawer}>
                  关闭
                </button>
              </div>
              <p className={styles.drawerSub}>
                {selected.documentId} · {selected.parseStatus} 解析 · 共 {chunks.length} 个切片
              </p>
              <div className={styles.drawerActions} style={{ display: "flex", gap: 8, marginBottom: 14 }}>
                <button
                  type="button"
                  className={styles.ghost}
                  onClick={() => void onReparse(selected)}
                >
                  重新解析
                </button>
                <button
                  type="button"
                  className={styles.ghost}
                  onClick={() => void onDelete(selected)}
                >
                  删除
                </button>
              </div>
              {chunksLoading ? (
                <p className={styles.empty}>切片加载中…</p>
              ) : chunks.length === 0 ? (
                <p className={styles.empty}>解析完成后这里会显示切片列表</p>
              ) : (
                <div className={styles.chunkList}>
                  {chunks.map((chunk) => (
                    <article key={chunk.chunkId} className={styles.chunkCard}>
                      <p className={styles.chunkIndex}>
                        片段 {chunk.seq} · {chunk.chunkId.slice(0, 10)}
                      </p>
                      <p className={styles.chunkContent}>{chunk.content}</p>
                    </article>
                  ))}
                </div>
              )}
            </aside>
          </>
        ) : null}
    </section>
  );
}
