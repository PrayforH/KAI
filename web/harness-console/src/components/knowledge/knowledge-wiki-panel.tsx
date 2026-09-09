"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  studioClient,
  type StudioKnowledgeWikiPage,
  type StudioKnowledgeWikiStats,
} from "../../lib/studio-client";
import { WikiEntityLink } from "./wiki-entity-link";
import styles from "./knowledge-wiki-panel.module.css";

const PAGE_TYPE_LABELS: Record<string, string> = {
  summary: "摘要",
  entity: "实体",
  concept: "概念",
  index: "索引",
  page: "页面",
};

const PAGE_TYPE_ORDER = ["index", "summary", "entity", "concept", "page"];

export function KnowledgeWikiPanel({
  reference,
  onOpenGraph,
}: {
  reference: string;
  onOpenGraph: (slug: string) => void;
}) {
  const [pages, setPages] = useState<StudioKnowledgeWikiPage[]>([]);
  const [stats, setStats] = useState<StudioKnowledgeWikiStats | null>(null);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<StudioKnowledgeWikiPage[] | null>(null);
  const [activeSlug, setActiveSlug] = useState<string | null>(null);
  const [page, setPage] = useState<StudioKnowledgeWikiPage | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [railMode, setRailMode] = useState<"index" | "tree">("index");
  const [typeTab, setTypeTab] = useState<"knowledge" | "summary">("knowledge");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [pagesValue, statsValue] = await Promise.all([
        studioClient.listWikiPages(reference),
        studioClient.getWikiStats(reference).catch(() => null),
      ]);
      setPages(pagesValue);
      setStats(statsValue);
      const index =
        pagesValue.find((item) => item.pageType === "index") ?? pagesValue[0] ?? null;
      if (index) {
        setActiveSlug(index.slug);
        setPage(index);
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "加载 Wiki 页面失败");
    } finally {
      setLoading(false);
    }
  }, [reference]);

  useEffect(() => {
    void load();
  }, [load]);

  const openPage = useCallback(
    async (slug: string) => {
      setActiveSlug(slug);
      setError("");
      try {
        setPage(await studioClient.getWikiPage(reference, slug));
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "打开 Wiki 页面失败");
      }
    },
    [reference],
  );

  const onSearch = useCallback(async () => {
    const value = query.trim();
    if (!value) {
      setResults(null);
      return;
    }
    setError("");
    try {
      setResults(await studioClient.searchWikiPages(reference, value));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "搜索 Wiki 页面失败");
    }
  }, [query, reference]);

  // WeKnora classifies wiki pages by category_path (e.g. 企业评分 > 科创评分).
  // Group by the first-level category so the rail matches the WeKnora console
  // instead of collapsing everything into 实体 / 概念.
  const grouped = useMemo(() => {
    const groups = new Map<string, StudioKnowledgeWikiPage[]>();
    for (const item of pages) {
      const category = item.categoryPath[0] || PAGE_TYPE_LABELS[item.pageType] || item.pageType;
      const bucket = groups.get(category) ?? [];
      bucket.push(item);
      groups.set(category, bucket);
    }
    return [...groups.entries()].sort(
      ([nameA, itemsA], [nameB, itemsB]) =>
        itemsB.length - itemsA.length || nameA.localeCompare(nameB, "zh-Hans-CN"),
    );
  }, [pages]);

  const rendered = useMemo(() => renderWikiContent(page?.content ?? ""), [page]);

  if (loading) {
    return <p className={styles.empty}>Wiki 索引加载中…</p>;
  }

  if (pages.length === 0) {
    return (
      <p className={styles.empty}>
        该知识库还没有 Wiki 页面。上传文档后，WeKnora 会按索引策略自动生成摘要、实体与概念页。
      </p>
    );
  }

  const list = results ?? pages;

  return (
    <div className={styles.layout}>
      <aside className={styles.rail}>
        <div className={styles.searchRow}>
          <input
            className={styles.search}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") void onSearch();
            }}
            placeholder="搜索 Wiki 页面…"
          />
          <button type="button" className={styles.railButton} onClick={() => void onSearch()}>
            搜索
          </button>
        </div>
        {results !== null ? (
          <button
            type="button"
            className={styles.clearRow}
            onClick={() => {
              setResults(null);
              setQuery("");
            }}
          >
            清除搜索（{results.length} 条结果）
          </button>
        ) : (
          <>
            <button
              type="button"
              className={`${styles.railEntry} ${railMode === "index" ? styles.railEntryActive : ""}`}
              onClick={() => {
                setRailMode("index");
                const index =
                  pages.find((item) => item.pageType === "index") ?? pages[0];
                if (index) void openPage(index.slug);
              }}
            >
              <span className={styles.railEntryGlyph} aria-hidden="true">
                <svg viewBox="0 0 20 20">
                  <path d="M4.5 4.5h7a3 3 0 0 1 3 3v8h-7a3 3 0 0 1-3-3z" />
                  <path d="M7.5 7.5h4m-4 3h4" />
                </svg>
              </span>
              索引
            </button>
            <button
              type="button"
              className={`${styles.railEntry} ${railMode === "tree" ? styles.railEntryActive : ""}`}
              onClick={() => setRailMode("tree")}
            >
              <span className={styles.railEntryGlyph} aria-hidden="true">
                <svg viewBox="0 0 20 20">
                  <path d="M4.5 4.5h11M4.5 10h11M4.5 15.5h7" />
                </svg>
              </span>
              目录
            </button>
            <div className={styles.railDivider} />
            <div className={styles.typeTabs}>
              <button
                type="button"
                className={typeTab === "knowledge" ? styles.typeTabActive : ""}
                onClick={() => setTypeTab("knowledge")}
              >
                知识{" "}
                {pages.filter((item) => item.pageType !== "summary" && item.pageType !== "index").length}
              </button>
              <button
                type="button"
                className={typeTab === "summary" ? styles.typeTabActive : ""}
                onClick={() => setTypeTab("summary")}
              >
                摘要 {pages.filter((item) => item.pageType === "summary").length}
              </button>
            </div>
          </>
        )}

        {results !== null ? (
          <ul className={styles.pageList}>
            {list.map((item) => (
              <li key={item.slug}>
                <button
                  type="button"
                  className={`${styles.pageItem} ${activeSlug === item.slug ? styles.pageItemActive : ""}`}
                  onClick={() => void openPage(item.slug)}
                >
                  <span className={styles.pageType}>
                    {PAGE_TYPE_LABELS[item.pageType] ?? item.pageType}
                  </span>
                  <span className={styles.pageTitle}>{item.title}</span>
                </button>
              </li>
            ))}
          </ul>
        ) : (
          grouped
            .map(([type, items]) => [
              type,
              items.filter((item) =>
                typeTab === "summary"
                  ? item.pageType === "summary"
                  : item.pageType !== "summary" && item.pageType !== "index",
              ),
            ] as const)
            .filter(([, items]) => items.length > 0)
            .map(([type, items]) => (
            <section key={type} className={styles.group}>
              <button
                type="button"
                className={styles.groupHead}
                onClick={() =>
                  setCollapsed((current) => ({ ...current, [type]: !current[type] }))
                }
              >
                <span className={styles.chevron}>{collapsed[type] ? "›" : "⌄"}</span>
                <span className={styles.groupName}>{PAGE_TYPE_LABELS[type] ?? type}</span>
                <span className={styles.groupCount}>{items.length}</span>
              </button>
              {collapsed[type] ? null : (
                <ul className={styles.pageList}>
                  {items.map((item) => (
                    <li key={item.slug}>
                      <button
                        type="button"
                        className={`${styles.pageItem} ${activeSlug === item.slug ? styles.pageItemActive : ""}`}
                        onClick={() => void openPage(item.slug)}
                      >
                        <span className={styles.pageTitle}>{item.title}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </section>
          ))
        )}
      </aside>

      <article className={styles.detail}>
        {error ? <p className={styles.error}>{error}</p> : null}
        {stats ? (
          <p className={styles.statLine}>
            共 {stats.totalPages} 页 · 引用 {stats.totalLinks} 条
          </p>
        ) : null}

        {page ? (
          <>
            <header className={styles.detailHead}>
              <div>
                <h2>{page.title}</h2>
                <p className={styles.detailMeta}>
                  <span className={styles.pageType}>
                    {PAGE_TYPE_LABELS[page.pageType] ?? page.pageType}
                  </span>
                  <code>{page.slug}</code>
                </p>
                {page.categoryPath.length > 0 ? (
                  <p className={styles.detailMeta}>
                    {page.categoryPath.join(" / ")}
                  </p>
                ) : null}
                {page.aliases.length > 0 ? (
                  <p className={styles.detailMeta}>别名：{page.aliases.join("、")}</p>
                ) : null}
              </div>
              <button
                type="button"
                className={styles.railButton}
                onClick={() => onOpenGraph(page.slug)}
              >
                在图谱中查看
              </button>
            </header>
            <div className={styles.content}>
              {rendered.map((line, index) => (
                <p
                  key={index}
                  className={
                    line.kind === "heading"
                      ? styles.paragraphHeading
                      : line.kind === "bullet"
                        ? styles.paragraphBullet
                        : styles.paragraph
                  }
                >
                  {line.segments.map((segment, segmentIndex) => {
                    if (segment.kind === "link") {
                      return (
                        <WikiEntityLink
                          key={segmentIndex}
                          onClick={() => void openPage(segment.slug)}
                        >
                          {segment.label}
                        </WikiEntityLink>
                      );
                    }
                    if (segment.kind === "bold") {
                      return <strong key={segmentIndex}>{segment.text}</strong>;
                    }
                    if (segment.kind === "code") {
                      return <code key={segmentIndex}>{segment.text}</code>;
                    }
                    return <span key={segmentIndex}>{segment.text}</span>;
                  })}
                </p>
              ))}
            </div>
          </>
        ) : (
          <p className={styles.empty}>从左侧选择一个 Wiki 页面</p>
        )}
      </article>
    </div>
  );
}

type InlineSegment =
  | { kind: "text"; text: string }
  | { kind: "link"; slug: string; label: string }
  | { kind: "bold"; text: string }
  | { kind: "code"; text: string };

type ContentLine = {
  kind: "heading" | "bullet" | "text";
  segments: InlineSegment[];
};

const WIKILINK = /\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g;

/** Split WeKnora wiki markdown into lines whose [[links]] stay inline in the
 * sentence, so a keyword never breaks onto its own line. */
export function renderWikiContent(content: string): ContentLine[] {
  const lines: ContentLine[] = [];
  for (const raw of content.replace(/\r\n/g, "\n").split("\n")) {
    const trimmed = raw.trim();
    if (!trimmed) continue;
    const heading = /^#{1,6}\s+(.*)$/.exec(trimmed);
    const bullet = /^[-*]\s+(.*)$/.exec(trimmed);
    const body = heading ? heading[1] : bullet ? bullet[1] : raw;
    const segments: InlineSegment[] = [];
    const pattern = /(\[\[[^\]]+\]\]|\*\*[^*]+\*\*|`[^`]+`)/g;
    let cursor = 0;
    let match: RegExpExecArray | null;
    while ((match = pattern.exec(body)) !== null) {
      const before = body.slice(cursor, match.index);
      if (before) segments.push({ kind: "text", text: before });
      const token = match[0];
      const link = /^\[\[([^\]|]+)(?:\|([^\]]+))?\]\]$/.exec(token);
      if (link) {
        segments.push({
          kind: "link",
          slug: link[1].trim(),
          label: (link[2] ?? link[1]).trim(),
        });
      } else if (token.startsWith("**")) {
        segments.push({ kind: "bold", text: token.slice(2, -2) });
      } else {
        segments.push({ kind: "code", text: token.slice(1, -1) });
      }
      cursor = match.index + token.length;
    }
    const after = body.slice(cursor);
    if (after) segments.push({ kind: "text", text: after });
    if (segments.length === 0) continue;
    lines.push({ kind: heading ? "heading" : bullet ? "bullet" : "text", segments });
  }
  return lines;
}
