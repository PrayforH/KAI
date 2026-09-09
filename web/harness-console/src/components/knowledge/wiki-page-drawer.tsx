"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  studioClient,
  type StudioKnowledgeWikiPage,
} from "../../lib/studio-client";
import styles from "./wiki-page-drawer.module.css";

const TYPE_LABELS: Record<string, string> = {
  summary: "摘要",
  entity: "实体",
  concept: "概念",
  index: "索引",
  page: "页面",
};

const TYPE_COLORS: Record<string, string> = {
  summary: "#4f8cff",
  entity: "#40c977",
  concept: "#e6aa4a",
  index: "#9a9a9a",
  page: "#737373",
};

function renderInline(
  text: string,
  onOpen: (slug: string) => void,
  keyPrefix: string,
): ReactNode[] {
  const nodes: ReactNode[] = [];
  const pattern = /(\[\[[^\]]+\]\]|\*\*[^*]+\*\*|`[^`]+`)/g;
  let cursor = 0;
  let match: RegExpExecArray | null;
  let index = 0;
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > cursor) {
      nodes.push(<span key={`${keyPrefix}-t${index++}`}>{text.slice(cursor, match.index)}</span>);
    }
    const token = match[0];
    const link = /^\[\[([^\]|]+)(?:\|([^\]]+))?\]\]$/.exec(token);
    if (link) {
      const slug = link[1].trim();
      nodes.push(
        <button
          key={`${keyPrefix}-l${index++}`}
          type="button"
          className={styles.link}
          onClick={() => onOpen(slug)}
        >
          {(link[2] ?? slug).trim()}
        </button>,
      );
    } else if (token.startsWith("**")) {
      nodes.push(<strong key={`${keyPrefix}-b${index++}`}>{token.slice(2, -2)}</strong>);
    } else {
      nodes.push(<code key={`${keyPrefix}-c${index++}`}>{token.slice(1, -1)}</code>);
    }
    cursor = match.index + token.length;
  }
  if (cursor < text.length) {
    nodes.push(<span key={`${keyPrefix}-tail`}>{text.slice(cursor)}</span>);
  }
  return nodes;
}

export function renderWikiBody(
  content: string,
  onOpen: (slug: string) => void,
): ReactNode {
  return content
    .replace(/\r\n/g, "\n")
    .split("\n")
    .map((line, index) => {
      if (!line.trim()) return null;
      const heading = /^(#{1,6})\s+(.*)$/.exec(line);
      if (heading) {
        return (
          <p key={index} className={styles.heading}>
            {renderInline(heading[2], onOpen, `h${index}`)}
          </p>
        );
      }
      const bullet = /^\s*[-*]\s+(.*)$/.exec(line);
      if (bullet) {
        return (
          <p key={index} className={styles.bullet}>
            {renderInline(bullet[1], onOpen, `b${index}`)}
          </p>
        );
      }
      return (
        <p key={index} className={styles.line}>
          {renderInline(line, onOpen, `p${index}`)}
        </p>
      );
    });
}

/**
 * Wiki page drawer shared by the knowledge console and chat answers.
 * Links inside the body keep opening pages in place, so a reader can follow a
 * chain of entities without leaving the drawer.
 */
export function WikiPageDrawer({
  reference,
  slug,
  onClose,
}: {
  /** Knowledge base that owns the page; resolved automatically when omitted
   * (a cited page stays openable after the composer selection is reset). */
  reference?: string | null;
  slug: string;
  onClose: () => void;
}) {
  const [current, setCurrent] = useState(slug);
  const [page, setPage] = useState<StudioKnowledgeWikiPage | null>(null);
  const [summaryOpen, setSummaryOpen] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [resolvedReference, setResolvedReference] = useState<string | null>(
    reference ?? null,
  );

  const open = useCallback(
    async (next: string) => {
      setCurrent(next);
      setSummaryOpen(false);
      setLoading(true);
      setError("");
      const candidates = resolvedReference
        ? [resolvedReference]
        : (await studioClient.listKnowledgeBases().catch(() => [])).map(
            (base) => base.reference,
          );
      let lastError = "打开 Wiki 页面失败";
      for (const candidate of candidates) {
        try {
          const found = await studioClient.getWikiPage(candidate, next);
          setResolvedReference(candidate);
          setPage(found);
          setLoading(false);
          return;
        } catch (cause) {
          lastError = cause instanceof Error ? cause.message : lastError;
        }
      }
      setError(lastError);
      setLoading(false);
    },
    [resolvedReference],
  );

  useEffect(() => {
    void open(slug);
  }, [open, slug]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <>
      <div className={styles.overlay} role="presentation" onClick={onClose} />
      <aside className={styles.drawer} aria-label="Wiki 页面详情">
        <header className={styles.head}>
          <div>
            <h3>{page?.title ?? current}</h3>
            {page ? (
              <p className={styles.meta}>
                <span
                  className={styles.badge}
                  style={{
                    color: TYPE_COLORS[page.pageType] ?? TYPE_COLORS.page,
                    borderColor: `${TYPE_COLORS[page.pageType] ?? TYPE_COLORS.page}66`,
                    background: `${TYPE_COLORS[page.pageType] ?? TYPE_COLORS.page}1f`,
                  }}
                >
                  {TYPE_LABELS[page.pageType] ?? page.pageType}
                </span>
                {page.categoryPath.length > 0 ? (
                  <span>{page.categoryPath.join(" / ")}</span>
                ) : null}
              </p>
            ) : null}
          </div>
          <button type="button" className={styles.close} onClick={onClose} aria-label="关闭">
            ×
          </button>
        </header>

        {error ? <p className={styles.error}>{error}</p> : null}
        {loading ? (
          <p className={styles.empty}>页面加载中…</p>
        ) : page ? (
          <>
            {page.summary ? (
              <div className={styles.summaryBox}>
                <p
                  className={`${styles.summary} ${summaryOpen ? styles.summaryOpen : ""}`}
                >
                  {page.summary}
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
            ) : null}
            <div className={styles.body}>{renderWikiBody(page.content, open)}</div>
          </>
        ) : null}
      </aside>
    </>
  );
}
