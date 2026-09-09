"use client";

import { useCallback, useEffect, useState } from "react";
import {
  studioClient,
  type StudioKnowledgeDocumentChunk,
} from "../../lib/studio-client";
import type { RunCitation } from "../../lib/run-view-model";
import { DrawerResizeHandle, useDrawerResize } from "../../lib/use-drawer-resize";
import styles from "./knowledge-citations.module.css";

export function KnowledgeCitations({ citations }: { citations: readonly RunCitation[] }) {
  const [open, setOpen] = useState<RunCitation | null>(null);
  const [chunk, setChunk] = useState<StudioKnowledgeDocumentChunk | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const { width, startResize } = useDrawerResize("citation", { min: 360, max: 1000 });

  const openCitation = useCallback(async (citation: RunCitation) => {
    setOpen(citation);
    setChunk(null);
    setError("");
    setLoading(true);
    try {
      setChunk(
        await studioClient.getKnowledgeDocumentChunk(citation.sourceReference, citation.chunkId),
      );
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "打开切片失败");
    } finally {
      setLoading(false);
    }
  }, []);

  const close = useCallback(() => {
    setOpen(null);
    setChunk(null);
    setError("");
  }, []);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") close();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [close, open]);

  if (citations.length === 0) return null;

  return (
    <div className={styles.row}>
      <span className={styles.label}>引用切片</span>
      <div className={styles.chips}>
        {citations.map((citation) => (
          <button
            key={`${citation.chunkId}-${citation.index}`}
            type="button"
            className={styles.chip}
            onClick={() => void openCitation(citation)}
            title={citation.title ?? citation.chunkId}
          >
            <span className={styles.chipIndex}>{citation.index}</span>
            <span className={styles.chipTitle}>
              {citation.title || citation.documentId || citation.chunkId}
            </span>
            {typeof citation.score === "number" ? (
              <span className={styles.chipScore}>{citation.score.toFixed(2)}</span>
            ) : null}
          </button>
        ))}
      </div>

      {open ? (
        <>
          <div className={styles.overlay} role="presentation" onClick={close} />
          <aside
            className={styles.drawer}
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
              <p className={styles.content}>{chunk?.content ?? open.content}</p>
            )}
            {chunk ? (
              <p className={styles.drawerFoot}>
                文档 {chunk.documentId} · 片段 {chunk.seq}
              </p>
            ) : null}
          </aside>
        </>
      ) : null}
    </div>
  );
}
