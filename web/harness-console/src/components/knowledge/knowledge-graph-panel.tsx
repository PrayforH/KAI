"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  studioClient,
  type StudioKnowledgeWikiGraph,
  type StudioKnowledgeWikiPage,
} from "../../lib/studio-client";
import styles from "./knowledge-graph-panel.module.css";

const TYPE_COLORS: Record<string, string> = {
  summary: "#4f8cff",
  entity: "#40c977",
  concept: "#e6aa4a",
  index: "#9a9a9a",
  page: "#737373",
};

const TYPE_LABELS: Record<string, string> = {
  summary: "摘要",
  entity: "实体",
  concept: "概念",
  index: "索引",
  page: "页面",
};

function renderWikiBody(
  content: string,
  onOpen: (slug: string) => void,
): ReactNode {
  const lines = content.replace(/\r\n/g, "\n").split("\n");
  return lines.map((line, index) => {
    if (!line.trim()) return null;
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      return (
        <p key={index} className={styles.bodyHeading}>
          {heading[2]}
        </p>
      );
    }
    const parts = line.split(/(\[\[[^\]]+\]\])/g);
    return (
      <p key={index} className={styles.bodyLine}>
        {parts.map((part, partIndex) => {
          const link = /^\[\[([^\]|]+)(?:\|([^\]]+))?\]\]$/.exec(part);
          if (!link) return <span key={partIndex}>{part}</span>;
          const slug = link[1].trim();
          return (
            <button
              key={partIndex}
              type="button"
              className={styles.bodyLink}
              onClick={() => onOpen(slug)}
            >
              {(link[2] ?? slug).trim()}
            </button>
          );
        })}
      </p>
    );
  });
}

export function KnowledgeGraphPanel({
  reference,
  focusSlug,
}: {
  reference: string;
  focusSlug?: string | null;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<unknown>(null);
  const [graph, setGraph] = useState<StudioKnowledgeWikiGraph | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [page, setPage] = useState<StudioKnowledgeWikiPage | null>(null);
  const [hiddenTypes, setHiddenTypes] = useState<Set<string>>(new Set());
  const [showArrows, setShowArrows] = useState(true);
  const [query, setQuery] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setGraph(await studioClient.getWikiGraph(reference));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "加载图谱失败");
    } finally {
      setLoading(false);
    }
  }, [reference]);

  useEffect(() => {
    void load();
  }, [load]);

  const openPage = useCallback(
    async (slug: string) => {
      setError("");
      try {
        setPage(await studioClient.getWikiPage(reference, slug));
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "打开页面失败");
      }
    },
    [reference],
  );

  const types = useMemo(() => {
    if (!graph) return [];
    return [...new Set(graph.nodes.map((node) => node.pageType))];
  }, [graph]);

  const visible = useMemo(() => {
    if (!graph) return null;
    const search = query.trim().toLowerCase();
    const nodes = graph.nodes.filter(
      (node) =>
        !hiddenTypes.has(node.pageType) &&
        (!search ||
          node.title.toLowerCase().includes(search) ||
          node.slug.toLowerCase().includes(search)),
    );
    const keep = new Set(nodes.map((node) => node.slug));
    return {
      nodes,
      links: graph.links.filter(([source, target]) => keep.has(source) && keep.has(target)),
    };
  }, [graph, hiddenTypes, query]);

  const fitView = useCallback(() => {
    const instance = graphRef.current as { fitView?: () => Promise<void> } | null;
    void instance?.fitView?.();
  }, []);

  useEffect(() => {
    if (!containerRef.current || !visible) return;
    let disposed = false;
    const container = containerRef.current;

    const draw = async () => {
      const { Graph } = await import("@antv/g6");
      if (disposed) return;
      const instance = new Graph({
        container,
        width: container.clientWidth,
        height: container.clientHeight || 560,
        autoFit: "view",
        data: {
          nodes: visible.nodes.map((node) => ({
            id: node.slug,
            data: { title: node.title, pageType: node.pageType },
          })),
          edges: visible.links.map(([source, target]) => ({ source, target })),
        },
        node: {
          style: {
            size: (datum: { data?: { pageType?: string } }) =>
              datum.data?.pageType === "summary" ? 26 : 18,
            fill: (datum: { data?: { pageType?: string } }) =>
              TYPE_COLORS[datum.data?.pageType ?? "page"] ?? TYPE_COLORS.page,
            labelText: (datum: { data?: { title?: string } }) => datum.data?.title ?? "",
            labelFill: "#d6d6d6",
            labelFontSize: 11,
            labelPlacement: "bottom",
            cursor: "pointer",
          },
        },
        edge: {
          style: {
            stroke: "#444444",
            lineWidth: 1,
            endArrow: showArrows,
          },
        },
        layout: {
          type: "force",
          preventOverlap: true,
          linkDistance: 120,
          nodeSize: 30,
        },
        behaviors: ["drag-canvas", "zoom-canvas", "drag-element"],
      });
      graphRef.current = instance;
      instance.on("node:click", (event) => {
        const target = (event as { target?: { id?: string } }).target;
        if (target?.id) void openPage(target.id);
      });
      await instance.render();
      if (focusSlug) {
        await instance.focusElement(focusSlug).catch(() => undefined);
      }
    };

    void draw();
    return () => {
      disposed = true;
      const instance = graphRef.current as { destroy?: () => void } | null;
      instance?.destroy?.();
      graphRef.current = null;
    };
  }, [visible, focusSlug, openPage, showArrows]);

  if (loading) {
    return <p className={styles.empty}>图谱加载中…</p>;
  }

  if (!graph || graph.nodes.length === 0) {
    return (
      <p className={styles.empty}>
        该知识库还没有 Wiki 引用关系图。启用 Wiki 索引策略并上传文档后会自动生成。
      </p>
    );
  }

  return (
    <div className={styles.layout}>
      {error ? <p className={styles.error}>{error}</p> : null}

      <div className={styles.canvasRow}>
        <div className={styles.canvasWrap}>
          <input
            className={styles.search}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索 Wiki 页面…"
          />
          <div ref={containerRef} className={styles.canvas} />
        </div>

        <aside className={styles.panel}>
          <ul className={styles.legendList}>
            {types.map((type) => (
              <li key={type}>
                <button
                  type="button"
                  className={`${styles.legendItem} ${hiddenTypes.has(type) ? styles.legendOff : ""}`}
                  onClick={() =>
                    setHiddenTypes((current) => {
                      const next = new Set(current);
                      if (next.has(type)) next.delete(type);
                      else next.add(type);
                      return next;
                    })
                  }
                >
                  <span
                    className={styles.dot}
                    style={{ background: TYPE_COLORS[type] ?? TYPE_COLORS.page }}
                  />
                  {TYPE_LABELS[type] ?? type}
                </button>
              </li>
            ))}
          </ul>

          <div className={styles.panelDivider} />

          <button type="button" className={styles.panelAction} onClick={fitView}>
            <span aria-hidden="true">⤢</span> 适应屏幕
          </button>
          <button
            type="button"
            className={styles.panelAction}
            onClick={() => setShowArrows((current) => !current)}
          >
            <span aria-hidden="true">↗</span> {showArrows ? "隐藏箭头" : "显示箭头"}
          </button>

          <div className={styles.panelDivider} />

          <p className={styles.panelStatTitle}>全库概览</p>
          <p className={styles.panelStatValue}>
            {visible?.nodes.length ?? 0} / {graph.nodes.length} 个节点
          </p>
          <p className={styles.panelStatHint}>
            {hiddenTypes.size === 0
              ? "已展示知识库全部节点"
              : `已隐藏 ${hiddenTypes.size} 类节点`}
          </p>
        </aside>

      </div>

      {page ? (
        <>
          <div
            className={styles.drawerOverlay}
            role="presentation"
            onClick={() => setPage(null)}
          />
          <aside className={styles.drawer} aria-label="Wiki 页面详情">
            <header className={styles.drawerHead}>
              <div>
                <h3>{page.title}</h3>
                <p className={styles.drawerMeta}>
                  <span className={styles.dot} style={{ background: TYPE_COLORS[page.pageType] }} />
                  {TYPE_LABELS[page.pageType] ?? page.pageType}
                  {page.categoryPath.length > 0
                    ? ` · ${page.categoryPath.join(" / ")}`
                    : ""}
                </p>
              </div>
              <button
                type="button"
                className={styles.close}
                onClick={() => setPage(null)}
                aria-label="关闭"
              >
                ×
              </button>
            </header>
            {page.summary ? <p className={styles.summary}>{page.summary}</p> : null}
            <div className={styles.content}>{renderWikiBody(page.content, openPage)}</div>
          </aside>
        </>
      ) : null}
    </div>
  );
}
