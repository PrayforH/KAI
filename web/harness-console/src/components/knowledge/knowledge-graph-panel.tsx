"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Graph } from "@antv/g6";
import {
  studioClient,
  type StudioKnowledgeWikiGraph,
} from "../../lib/studio-client";
import { WikiPageDrawer } from "./wiki-page-drawer";
import styles from "./knowledge-graph-panel.module.css";

// Graph colors encode node types; management controls keep the neutral theme.
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

type GraphDatum = {
  title?: string;
  pageType?: string;
  degree?: number;
  endArrow?: boolean;
};

/** Single click and double click share one physical click; hold single clicks
 * briefly so a double click expands the neighborhood instead of opening the
 * page drawer. */
const CLICK_DELAY_MS = 240;

export function KnowledgeGraphPanel({
  reference,
  focusSlug,
}: {
  reference: string;
  focusSlug?: string | null;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const graphRef = useRef<Graph | null>(null);
  const clickTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const selectedRef = useRef<string | null>(null);
  const firstRenderRef = useRef(true);
  const showArrowsRef = useRef(true);
  const [graph, setGraph] = useState<StudioKnowledgeWikiGraph | null>(null);
  const [graphReady, setGraphReady] = useState(false);
  const [graphVersion, setGraphVersion] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [pageSlug, setPageSlug] = useState<string | null>(null);
  const [hiddenTypes, setHiddenTypes] = useState<Set<string>>(new Set());
  const [showArrows, setShowArrows] = useState(true);
  const [revealed, setRevealed] = useState<Set<string> | null>(null);
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

  const openPage = useCallback((slug: string) => setPageSlug(slug), []);

  const types = useMemo(() => {
    if (!graph) return [];
    return [...new Set(graph.nodes.map((node) => node.pageType))];
  }, [graph]);

  // Undirected adjacency over the whole base; expansion always reveals every
  // neighbour of a node regardless of link direction.
  const adjacency = useMemo(() => {
    const map = new Map<string, string[]>();
    if (!graph) return map;
    for (const [source, target] of graph.links) {
      if (source !== target) {
        (map.get(source) ?? map.set(source, []).get(source))?.push(target);
        (map.get(target) ?? map.set(target, []).get(target))?.push(source);
      }
    }
    return map;
  }, [graph]);

  const degreeOf = useMemo(() => {
    const counts = new Map<string, number>();
    if (!graph) return counts;
    for (const [source, target] of graph.links) {
      if (source === target) continue;
      counts.set(source, (counts.get(source) ?? 0) + 1);
      counts.set(target, (counts.get(target) ?? 0) + 1);
    }
    return counts;
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

  // Explore mode: only the revealed set stays on canvas; double clicking a
  // node reveals one more hop, so the graph grows outward step by step.
  const shown = useMemo(() => {
    if (!visible) return null;
    if (!revealed) return visible;
    const nodes = visible.nodes.filter((node) => revealed.has(node.slug));
    const keep = new Set(nodes.map((node) => node.slug));
    return {
      nodes,
      links: visible.links.filter(([source, target]) => keep.has(source) && keep.has(target)),
    };
  }, [visible, revealed]);

  const expand = useCallback(
    (slug: string) => {
      setRevealed((current) => {
        const next = new Set(current ?? [slug]);
        next.add(slug);
        for (const neighbour of adjacency.get(slug) ?? []) next.add(neighbour);
        return next;
      });
    },
    [adjacency],
  );

  const fitView = useCallback(() => {
    const instance = graphRef.current;
    void instance?.fitView({ when: "always" }, { duration: 320, easing: "ease-in-out" });
  }, []);

  // The instance is created once per mount; data updates flow through setData
  // so filters and expansions animate instead of rebuilding the canvas.
  useEffect(() => {
    if (!graphReady || !containerRef.current || graphRef.current) return;
    let disposed = false;
    const container = containerRef.current;

    const draw = async () => {
      const { Graph: G6Graph } = await import("@antv/g6");
      if (disposed) return;
      const instance = new G6Graph({
        container,
        width: container.clientWidth,
        height: container.clientHeight || 560,
        // Element updates (data changes, state flips) animate globally.
        animation: true,
        data: { nodes: [], edges: [] },
        node: {
          style: {
            size: (datum: { data?: GraphDatum }) =>
              datum.data?.pageType === "summary"
                ? 30
                : 18 + Math.min(datum.data?.degree ?? 0, 6) * 2,
            fill: (datum: { data?: GraphDatum }) =>
              TYPE_COLORS[datum.data?.pageType ?? "page"] ?? TYPE_COLORS.page,
            fillOpacity: 0.95,
            lineWidth: 1,
            stroke: "rgb(255 255 255 / 16%)",
            labelText: (datum: { data?: GraphDatum }) => datum.data?.title ?? "",
            labelFill: "#c9c9c9",
            labelFontSize: 11,
            labelPlacement: "bottom",
            // Labels carry a dark plate so they stay readable over edges.
            labelBackground: true,
            labelBackgroundFill: "rgb(18 18 18 / 78%)",
            labelBackgroundRadius: 4,
            labelPadding: [1, 4],
            cursor: "pointer",
          },
          state: {
            active: {
              halo: true,
              haloLineWidth: 3,
              haloStrokeOpacity: 0.22,
              labelFill: "#ffffff",
              labelFontWeight: 600,
              lineWidth: 1.5,
            },
            dim: {
              fillOpacity: 0.14,
              labelOpacity: 0.25,
              strokeOpacity: 0.1,
            },
            selected: {
              halo: true,
              haloLineWidth: 4,
              haloStrokeOpacity: 0.3,
              stroke: "#ececec",
              labelFill: "#ffffff",
              labelFontWeight: 600,
            },
          },
        },
        edge: {
          style: {
            stroke: "#3f3f3f",
            lineWidth: 1,
            endArrow: (datum: { data?: GraphDatum }) => Boolean(datum.data?.endArrow),
            endArrowSize: 6,
            endArrowFill: "#4a4a4a",
          },
          state: {
            active: { stroke: "#9a9a9a", lineWidth: 1.6, endArrowFill: "#9a9a9a" },
            dim: { strokeOpacity: 0.08, endArrowFill: "rgb(140 140 140 / 8%)" },
          },
        },
        layout: {
          type: "force",
          // The simulation animates between ticks, so the graph settles
          // organically instead of snapping into place.
          animation: true,
          preventOverlap: true,
          linkDistance: 130,
          nodeSize: 30,
        },
        behaviors: [
          "drag-canvas",
          "zoom-canvas",
          "drag-element",
          // Hover lights up the 1-hop neighbourhood and dims the rest.
          { type: "hover-activate", degree: 1, state: "active", inactiveState: "dim" },
        ],
      });
      graphRef.current = instance;
      const onNodeActivate = (event: unknown) => {
        const target = (event as { target?: { id?: string | number } }).target;
        if (!target?.id) return;
        const slug = String(target.id);
        if (clickTimer.current) clearTimeout(clickTimer.current);
        clickTimer.current = setTimeout(() => openPage(slug), CLICK_DELAY_MS);
      };
      const onNodeExpand = (event: unknown) => {
        const target = (event as { target?: { id?: string | number } }).target;
        if (!target?.id) return;
        if (clickTimer.current) {
          clearTimeout(clickTimer.current);
          clickTimer.current = null;
        }
        expand(String(target.id));
      };
      instance.on("node:click", onNodeActivate);
      instance.on("node:dblclick", onNodeExpand);
      await instance.render();
      if (disposed) return;
      setGraphVersion((version) => version + 1);
    };

    void draw();
    return () => {
      disposed = true;
      if (clickTimer.current) clearTimeout(clickTimer.current);
      const instance = graphRef.current;
      instance?.destroy?.();
      graphRef.current = null;
      selectedRef.current = null;
      firstRenderRef.current = true;
    };
  }, [graphReady, expand, openPage]);

  useEffect(() => {
    setGraphReady(Boolean(visible));
  }, [visible]);

  const markSelected = useCallback((instance: Graph, slug: string | null) => {
    const previous = selectedRef.current;
    if (previous && previous !== slug) {
      try {
        void instance.setElementState(previous, [], true);
      } catch {
        // The previous selection may already be hidden in explore mode.
      }
    }
    if (slug) {
      try {
        void instance.setElementState(slug, ["selected"], true);
      } catch {
        // focusSlug might not be part of the filtered view.
      }
    }
    selectedRef.current = slug;
  }, []);

  useEffect(() => {
    const instance = graphRef.current;
    if (!instance || !shown || shown.nodes.length === 0) return;
    // Surviving nodes keep their current canvas position (drags included);
    // only genuinely new nodes get laid out, so expansions stay put.
    const positionOf = (slug: string) => {
      try {
        const point = instance.getElementPosition(slug);
        const [x, y] = point;
        return { x, y };
      } catch {
        return undefined;
      }
    };
    instance.setData({
      nodes: shown.nodes.map((node) => {
        const position = positionOf(node.slug);
        return {
          id: node.slug,
          data: {
            title: node.title,
            pageType: node.pageType,
            degree: degreeOf.get(node.slug) ?? 0,
          },
          ...(position ? { style: { x: position.x, y: position.y } } : {}),
        };
      }),
      edges: shown.links.map(([source, target], index) => ({
        id: `link-${index}`,
        source,
        target,
        data: { endArrow: showArrowsRef.current },
      })),
    });
    selectedRef.current = null;
    void instance.render().then(() => {
      if (firstRenderRef.current) {
        firstRenderRef.current = false;
        void instance.fitView({ when: "always" });
      }
      if (focusSlug && shown.nodes.some((node) => node.slug === focusSlug)) {
        markSelected(instance, focusSlug);
        void instance.focusElement(focusSlug, { duration: 380, easing: "ease-out" });
      }
    });
  }, [shown, focusSlug, graphVersion, degreeOf, markSelected]);

  // Arrow toggles restyle existing edges in place; no re-layout, no reshuffle.
  useEffect(() => {
    showArrowsRef.current = showArrows;
    const instance = graphRef.current;
    if (!instance) return;
    const edges = instance.getEdgeData();
    if (edges.length === 0) return;
    instance.updateEdgeData(
      edges.map((edge) => ({ id: edge.id, data: { endArrow: showArrows } })),
    );
    void instance.render();
  }, [showArrows]);

  useEffect(() => {
    return () => {
      if (clickTimer.current) clearTimeout(clickTimer.current);
    };
  }, []);

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
          <span className={styles.hint}>单击打开页面 · 双击展开邻居</span>
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
          {revealed ? (
            <button
              type="button"
              className={styles.panelAction}
              onClick={() => setRevealed(null)}
            >
              <span aria-hidden="true">↺</span> 显示全部
            </button>
          ) : null}

          <div className={styles.panelDivider} />

          <p className={styles.panelStatTitle}>全库概览</p>
          <p className={styles.panelStatValue}>
            {shown?.nodes.length ?? 0} / {graph.nodes.length} 个节点
          </p>
          <p className={styles.panelStatHint}>
            {revealed
              ? "双击节点继续展开邻居"
              : hiddenTypes.size === 0
                ? "已展示知识库全部节点"
                : `已隐藏 ${hiddenTypes.size} 类节点`}
          </p>
        </aside>

      </div>

      {pageSlug ? <WikiPageDrawer reference={reference} slug={pageSlug} onClose={() => setPageSlug(null)} /> : null}
    </div>
  );
}
