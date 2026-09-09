"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  studioClient,
  type StudioKnowledgeDocumentChunk,
} from "../../lib/studio-client";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { useAnswerCitations } from "./answer-citation-context";
import type { RunCitation } from "../../lib/run-view-model";
import { DrawerResizeHandle, useDrawerResize } from "../../lib/use-drawer-resize";
import { KnowledgeDrawerLayer } from "./knowledge-drawer-layer";
import styles from "./knowledge-citations.module.css";

export function KnowledgeCitations({ citations, showSources = true }: { citations: readonly RunCitation[]; showSources?: boolean }) {
  const answer = useAnswerCitations();
  const requestId = useRef(0);
  const [open, setOpen] = useState<RunCitation | null>(null);
  const [chunk, setChunk] = useState<StudioKnowledgeDocumentChunk | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const { width, startResize } = useDrawerResize("citation", { min: 360, max: 1000 });

  const openCitation = useCallback(async (citation: RunCitation) => {
    const id = ++requestId.current;
    setOpen(citation);
    setChunk(null);
    setError("");
    setLoading(true);
    try {
      const result = await studioClient.getKnowledgeDocumentChunk(citation.sourceReference, citation.chunkId);
      if (id === requestId.current) setChunk(result);
    } catch (cause) {
      if (id === requestId.current) setError(cause instanceof Error ? cause.message : "打开切片失败");
    } finally {
      if (id === requestId.current) setLoading(false);
    }
  }, []);

  const close = useCallback(() => {
    requestId.current += 1;
    setOpen(null);
    setChunk(null);
    setError("");
  }, []);

  const requested = answer?.requested;
  const request = answer?.request;
  useEffect(() => {
    if (!showSources && requested) { void openCitation(requested); request?.(null); }
  }, [requested, request, openCitation, showSources]);
  useEffect(() => () => { requestId.current += 1; }, []);


  if (citations.length === 0) return null;

  return (
    <>
      {showSources ? <div className={styles.row}>
      <span className={styles.label}>检索来源</span>
      <div className={styles.chips}>
        {citations.map((citation) => (
          <button
            key={`${citation.sourceReference}-${citation.chunkId}`}
            type="button"
            className={styles.chip}
            onClick={() => void openCitation(citation)}
            title={citation.title ?? citation.chunkId}
          >
            <span className={styles.chipIndex}>{citation.index}</span>
            <span className={styles.chipTitle}>
              {citation.title || citation.documentId || citation.chunkId}
            </span>

          </button>
        ))}
      </div>

      </div> : null}
      {open ? (
        <KnowledgeDrawerLayer onClose={close}>
          <div className={styles.overlay} role="presentation" onClick={close} />
          <aside
            className={styles.drawer}
            role="dialog"
            aria-modal="true"
            aria-label="引用切片详情"
            style={{ width }}
          >
            <DrawerResizeHandle onPointerDown={startResize} className={styles.resizeHandle} />
            <header className={styles.drawerHead}>
              <div>
                <h3>{open.title || "引用切片"}</h3>
                <p className={styles.drawerMeta}>
                  {open.sourceDisplayName ? <span>{open.sourceDisplayName}</span> : null}
                  <code>{open.chunkId}</code>
                </p>
              </div>
              <button type="button" className={styles.close} onClick={close}>
                关闭
              </button>
            </header>
            {error ? <p className={styles.error}>{error}</p> : null}
            {loading ? (
              <p className={styles.empty}>切片加载中…</p>
            ) : (
              <div className={`${styles.content} aui-md`}><ReactMarkdown remarkPlugins={[remarkGfm]}>{chunk?.content ?? open.content}</ReactMarkdown></div>
            )}
            {chunk ? (
              <p className={styles.drawerFoot}>
                文档 {chunk.documentId} · 片段 {chunk.seq}
              </p>
            ) : null}
          </aside>
        </KnowledgeDrawerLayer>
      ) : null}
    </>
  );
}
