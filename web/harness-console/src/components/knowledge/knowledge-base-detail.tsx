"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  studioClient,
  type StudioKnowledgeBase,
  type StudioKnowledgeDocumentChunk,
  type StudioKnowledgeDocumentStatus,
} from "../../lib/studio-client";
import { KnowledgeGraphPanel } from "./knowledge-graph-panel";
import { KnowledgeMembersPanel } from "./knowledge-members-panel";
import { KnowledgeWikiPanel } from "./knowledge-wiki-panel";
import styles from "./knowledge-base-detail.module.css";

type Tab = "docs" | "wiki" | "graph" | "members";

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
            <p className={styles.headDesc}>
              {base.reference} · {base.engine === "weknora" ? "WeKnora" : "内置"} ·{" "}
              {base.kbType} 知识库
            </p>
            {base.description ? <p className={styles.headDesc}>{base.description}</p> : null}
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
          <button
            type="button"
            className={`${styles.tab} ${tab === "members" ? styles.tabActive : ""}`}
            onClick={() => setTab("members")}
          >
            成员管理
          </button>
        </div>

        {error ? <p className={styles.error}>{error}</p> : null}
        {notice ? <p className={styles.notice}>{notice}</p> : null}

        {tab === "docs" ? (
          <section>
            {documents.length === 0 ? (
              <p className={styles.empty}>
                还没有文档。上传文件或手动创建文档，WeKnora 将自动解析并切片。
              </p>
            ) : (
              <div className={styles.docList}>
                {documents.map((doc) => (
                  <button
                    key={doc.documentId}
                    type="button"
                    className={styles.docRow}
                    onClick={() => void openDocument(doc)}
                  >
                    <div style={{ minWidth: 0 }}>
                      <p className={styles.docTitle}>{doc.title}</p>
                    </div>
                    <div className={styles.docMeta}>
                      {doc.fileType ? <span>{doc.fileType}</span> : null}
                      <span
                        className={`${styles.statusChip} ${
                          doc.parseStatus === "completed"
                            ? styles.statusCompleted
                            : doc.parseStatus === "processing" || doc.parseStatus === "pending"
                              ? styles.statusProcessing
                              : styles.statusFailed
                        }`}
                      >
                        {PARSE_LABELS[doc.parseStatus] ?? doc.parseStatus}
                      </span>
                      <span>{doc.summaryStatus === "completed" ? "含摘要" : ""}</span>
                    </div>
                  </button>
                ))}
              </div>
            )}
          </section>
        ) : tab === "wiki" ? (
          <KnowledgeWikiPanel
            reference={reference}
            onOpenGraph={(slug) => {
              setGraphFocus(slug);
              setTab("graph");
            }}
          />
        ) : tab === "graph" ? (
          <KnowledgeGraphPanel reference={reference} focusSlug={graphFocus} />
        ) : (
          <KnowledgeMembersPanel reference={reference} />
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
