"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  studioClient,
  type StudioKnowledgeWikiPage,
} from "../../lib/studio-client";
import { DrawerResizeHandle, useDrawerResize } from "../../lib/use-drawer-resize";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { knowledgeUrlTransform, remarkWikiLinks } from "../../lib/knowledge-links";
import { WikiEntityLink } from "./wiki-entity-link";
import { KnowledgeDrawerLayer } from "./knowledge-drawer-layer";
import styles from "./wiki-page-drawer.module.css";

const TYPE_LABELS: Record<string, string> = {
  summary: "摘要",
  entity: "实体",
  concept: "概念",
  index: "索引",
  page: "页面",
};

const TYPE_COLORS: Record<string, string> = {
  summary: "#d6d6d6",
  entity: "#b3b3b3",
  concept: "#8c8c8c",
  index: "#9a9a9a",
  page: "#737373",
};

export function renderWikiBody(content: string, onOpen: (slug: string) => void): ReactNode {
  return <ReactMarkdown remarkPlugins={[remarkGfm, remarkWikiLinks]} urlTransform={knowledgeUrlTransform} components={{
    a: ({ href, children }) => {
      if (href?.startsWith("wiki:")) {
        let target: string;
        try { target = decodeURIComponent(href.slice(5)); } catch { return <span>{children}</span>; }
        return <WikiEntityLink onClick={() => onOpen(target)}>{children}</WikiEntityLink>;
      }
      return <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>;
    },
    table: (props) => <div className="aui-table-scroll"><table {...props} /></div>,
  }}>{content}</ReactMarkdown>;
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
  const resolvedReference = useRef<string | null>(reference ?? null);
  const requestId = useRef(0);
  const { width, startResize } = useDrawerResize("wiki", { min: 360, max: 1000 });

  const open = useCallback(
    async (next: string) => {
      const id = ++requestId.current;
      setPage(null);
      setCurrent(next);
      setSummaryOpen(false);
      setLoading(true);
      setError("");
      const candidates = resolvedReference.current
        ? [resolvedReference.current]
        : (await studioClient.listKnowledgeBases().catch(() => [])).map(
            (base) => base.reference,
          );
      let lastError = "未找到 Wiki 页面或没有查看权限";
      const matches: Array<{ reference: string; page: StudioKnowledgeWikiPage }> = [];
      for (const candidate of candidates) {
        try {
          const found = await studioClient.getWikiPage(candidate, next);
          if (id !== requestId.current) return;
          matches.push({ reference: candidate, page: found });
        } catch (cause) {
          lastError = cause instanceof Error ? cause.message : lastError;
        }
      }
      if (id !== requestId.current) return;
      if (matches.length === 1) {
        resolvedReference.current = matches[0].reference;
        setPage(matches[0].page);
      } else {
        setError(matches.length > 1 ? "多个知识库存在同名页面，旧引用未记录所属知识库，无法确定来源。请重新提问获取明确引用。" : lastError);
      }
      setLoading(false);
    },
    [],
  );

  useEffect(() => {
    resolvedReference.current = reference ?? null;
    void open(slug);
    return () => { requestId.current += 1; };
  }, [open, slug, reference]);


  return (
    <KnowledgeDrawerLayer onClose={onClose}>
      <div className={styles.overlay} role="presentation" onClick={onClose} />
      <aside
        className={styles.drawer}
        role="dialog"
        aria-modal="true"
        aria-label="Wiki 页面详情"
        style={{ width }}
      >
        <DrawerResizeHandle onPointerDown={startResize} className={styles.resizeHandle} />
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
            <div className={`${styles.body} aui-md`}>{renderWikiBody(page.content, open)}</div>
          </>
        ) : null}
      </aside>
    </KnowledgeDrawerLayer>
  );
}
