"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { RunCitation } from "../../lib/run-view-model";
import { citationTarget } from "../../lib/knowledge-links";
import { useAnswerCitations } from "./answer-citation-context";
import { CitationDocumentDrawer } from "./citation-document-drawer";
import styles from "./knowledge-citations.module.css";

export function KnowledgeCitations({ citations, showSources = true }: { citations: readonly RunCitation[]; showSources?: boolean }) {
  const answer = useAnswerCitations();
  const [open, setOpen] = useState<RunCitation | null>(null);
  const close = useCallback(() => setOpen(null), []);

  const requested = answer?.requested;
  const request = answer?.request;
  useEffect(() => {
    if (!showSources && requested) { setOpen(requested); request?.(null); }
  }, [requested, request, showSources]);

  if (citations.length === 0 && !open) return null;

  return (
    <>
      {showSources && citations.length > 0 ? <div className={styles.row}>
      <span className={styles.label}>检索来源</span>
      <div className={styles.chips}>
        {citations.map((citation) => (
          <button
            key={`${citation.sourceReference}-${citation.chunkId}`}
            type="button"
            className={styles.chip}
            onClick={() => setOpen(citation)}
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
        <CitationDocumentDrawer key={citationTarget(open)} citation={open} onClose={close} />
      ) : null}
    </>
  );
}
