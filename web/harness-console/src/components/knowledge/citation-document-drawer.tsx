"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  studioClient,
  type StudioKnowledgeDocumentChunk,
  type StudioKnowledgeDocumentStatus,
} from "../../lib/studio-client";
import type { RunCitation } from "../../lib/run-view-model";
import { DrawerResizeHandle, useDrawerResize } from "../../lib/use-drawer-resize";
import { KnowledgeDrawerLayer } from "./knowledge-drawer-layer";
import styles from "./citation-document-drawer.module.css";

const PARSE_LABELS: Record<string, string> = {
  pending: "排队中",
  processing: "解析中",
  completed: "已完成",
  failed: "失败",
};

type DocumentView = "full" | "chunks";

/**
 * A RAG citation opens as the document it was taken from: the full text with the
 * cited slice marked, and a chunk view for reading the surrounding slices. The
 * Wiki drawer stays separate — a Wiki hit is a synthesized page, not a document.
 */
export function CitationDocumentDrawer({
  citation,
  onClose,
}: {
  citation: RunCitation;
  onClose: () => void;
}) {
  const requestId = useRef(0);
  const citedRef = useRef<HTMLElement | null>(null);
  const [document, setDocument] = useState<StudioKnowledgeDocumentStatus | null>(null);
  const [chunks, setChunks] = useState<StudioKnowledgeDocumentChunk[]>([]);
  const [view, setView] = useState<DocumentView>("full");
  const [summaryOpen, setSummaryOpen] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const { width, startResize } = useDrawerResize("citation", { min: 360, max: 1000 });

  const documentId = citation.documentId;

  useEffect(() => {
    const id = ++requestId.current;
    setLoading(true);
    setError("");
    setChunks([]);
    setDocument(null);
    setView("full");
    void (async () => {
      // The document proxy is engine-backed; a legacy snapshot citation has no
      // document to open, so only the excerpt that arrived with the answer is shown.
      const [meta, listed] = documentId
        ? await Promise.all([
            studioClient
              .getKnowledgeDocument(citation.sourceReference, documentId)
              .catch(() => null),
            studioClient
              .listKnowledgeDocumentChunks(citation.sourceReference, documentId)
              .catch(() => null),
          ])
        : [null, null];
      if (id !== requestId.current) return;
      if (meta) setDocument(meta);
      if (listed && listed.length > 0) {
        setChunks([...listed].sort((left, right) => left.seq - right.seq));
      } else {
        const single = await studioClient
          .getKnowledgeDocumentChunk(citation.sourceReference, citation.chunkId)
          .catch(() => null);
        if (id !== requestId.current) return;
        if (single) setChunks([{ ...single, chunkId: single.chunkId || citation.chunkId }]);
        else if (!citation.content) setError("无法打开该引用的文档内容");
      }
      if (id === requestId.current) setLoading(false);
    })();
    return () => {
      requestId.current += 1;
    };
  }, [citation.chunkId, citation.content, citation.sourceReference, documentId]);

  // Bring the cited slice into view once its text has been rendered.
  useEffect(() => {
    if (loading || !citedRef.current) return;
    citedRef.current.scrollIntoView({ block: "center" });
  }, [loading, view]);

  const attachCited = useCallback((node: HTMLElement | null) => {
    citedRef.current = node;
  }, []);

  const citedIndex = chunks.findIndex((chunk) => chunk.chunkId === citation.chunkId);
  const citedSeq = citedIndex >= 0 ? chunks[citedIndex].seq : undefined;
  const title = document?.title || citation.title || documentId || "引用文档";

  const chunkBody = (chunk: StudioKnowledgeDocumentChunk) => (
    <ReactMarkdown remarkPlugins={[remarkGfm]}>{chunk.content}</ReactMarkdown>
  );

  return (
    <KnowledgeDrawerLayer onClose={onClose}>
      <div className={styles.overlay} role="presentation" onClick={onClose} />
      <aside
        className={styles.drawer}
        role="dialog"
        aria-modal="true"
        aria-label="引用文档详情"
        style={{ width }}
      >
        <DrawerResizeHandle onPointerDown={startResize} className={styles.resizeHandle} />
        <header className={styles.head}>
          <span className={styles.glyph} aria-hidden="true">
            <svg viewBox="0 0 20 20">
              <path d="M4.5 3.5h7l4 4v9h-11z" />
              <path d="M11.5 3.5v4h4M7.5 11h5m-5 2.8h5" />
            </svg>
          </span>
          <div className={styles.headText}>
            <h3>{title}</h3>
            <p className={styles.meta}>
              {citation.sourceDisplayName ? (
                <span className={styles.base}>{citation.sourceDisplayName}</span>
              ) : null}
              {document?.fileType ? (
                <span className={styles.badge}>{document.fileType.toUpperCase()}</span>
              ) : null}
              {document?.parseStatus ? (
                <span className={styles.badge}>
                  {PARSE_LABELS[document.parseStatus] ?? document.parseStatus}
                </span>
              ) : null}
              {citedSeq === undefined ? null : <span>引用片段 {citedSeq}</span>}
            </p>
          </div>
          <button type="button" className={styles.close} onClick={onClose} aria-label="关闭">
            ×
          </button>
        </header>

        {error ? <p className={styles.error}>{error}</p> : null}
        {loading ? (
          <p className={styles.empty}>文档加载中…</p>
        ) : chunks.length === 0 ? (
          <div className={`${styles.excerpt} aui-md`}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{citation.content}</ReactMarkdown>
          </div>
        ) : (
          <>
            <section className={styles.section}>
              <div className={styles.sectionHead}>
                <h4 className={styles.sectionTitle}>文档内容</h4>
                <span className={styles.count}>共 {chunks.length} 个片段</span>
                <div className={styles.toggle}>
                  <button
                    type="button"
                    className={view === "full" ? styles.toggleActive : ""}
                    onClick={() => setView("full")}
                  >
                    全文
                  </button>
                  <button
                    type="button"
                    className={view === "chunks" ? styles.toggleActive : ""}
                    onClick={() => setView("chunks")}
                  >
                    查看分块
                  </button>
                </div>
              </div>
              {citedIndex < 0 && citation.content ? (
                <div className={styles.unmatched}>
                  <span className={styles.citedMark}>引用摘录 · 当前解析结果中未匹配到该切片</span>
                  <div className={styles.chunkContent}>
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{citation.content}</ReactMarkdown>
                  </div>
                </div>
              ) : null}
              {view === "full" ? (
                <div className={styles.fullText}>
                  {chunks.map((chunk) => {
                    const cited = chunk.chunkId === citation.chunkId;
                    return (
                      <div
                        key={chunk.chunkId}
                        ref={cited ? attachCited : undefined}
                        className={cited ? styles.citedBlock : undefined}
                      >
                        {cited ? (
                          <span className={styles.citedMark}>本次引用 · 片段 {chunk.seq}</span>
                        ) : null}
                        {chunkBody(chunk)}
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className={styles.chunkList}>
                  {chunks.map((chunk) => {
                    const cited = chunk.chunkId === citation.chunkId;
                    return (
                      <article
                        key={chunk.chunkId}
                        ref={cited ? attachCited : undefined}
                        className={`${styles.chunkCard} ${cited ? styles.chunkCited : ""}`}
                      >
                        <p className={styles.chunkIndex}>
                          片段 {chunk.seq}
                          {cited ? <span className={styles.chunkTag}>本次引用</span> : null}
                        </p>
                        <div className={styles.chunkContent}>{chunkBody(chunk)}</div>
                      </article>
                    );
                  })}
                </div>
              )}
            </section>

            {document?.description ? (
              <section className={styles.section}>
                <h4 className={styles.sectionTitle}>摘要</h4>
                <div className={styles.summaryBox}>
                  <p className={`${styles.summary} ${summaryOpen ? styles.summaryOpen : ""}`}>
                    {document.description}
                  </p>
                  <button
                    type="button"
                    className={styles.summaryToggle}
                    aria-expanded={summaryOpen}
                    aria-label={summaryOpen ? "收起摘要" : "展开摘要"}
                    onClick={() => setSummaryOpen((value) => !value)}
                  >
                    <svg viewBox="0 0 16 16" aria-hidden="true">
                      <path d="m4.5 6.5 3.5 3.5 3.5-3.5" />
                    </svg>
                  </button>
                </div>
              </section>
            ) : null}

            <p className={styles.foot}>
              {document?.createdAt ? <span>{formatDate(document.createdAt)}</span> : null}
              <span>文档 {documentId}</span>
              {citedSeq === undefined ? null : <span>引用片段 {citedSeq}</span>}
            </p>
          </>
        )}
      </aside>
    </KnowledgeDrawerLayer>
  );
}

function formatDate(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "";
  const pad = (input: number) => String(input).padStart(2, "0");
  return `${String(parsed.getFullYear()).slice(2)}-${pad(parsed.getMonth() + 1)}-${pad(
    parsed.getDate(),
  )} ${pad(parsed.getHours())}:${pad(parsed.getMinutes())}`;
}
