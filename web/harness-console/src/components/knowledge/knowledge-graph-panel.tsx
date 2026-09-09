"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Graph } from "@antv/g6";
import type { ForceGraph3DInstance, NodeObject } from "3d-force-graph";
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

type ViewMode = "2d" | "3d";

const MODE_STORAGE_KEY = "knowledge-graph-view-mode";

/** Single click and double click share one physical click; hold single clicks
 * briefly so a double click expands the neighborhood instead of opening the
 * page drawer. */
const CLICK_DELAY_MS = 240;

/** Double-click window for the expand-neighbour gesture. */
const DBLCLICK_MS = 320;

type Node3D = {
  id: string;
  title: string;
  pageType: string;
  val: number;
  x?: number;
  y?: number;
  z?: number;
};

type LinkEndpoint = string | number | Node3D | undefined;

type Link3D = { source: LinkEndpoint; target: LinkEndpoint };

type NodeVisual = {
  material: { opacity: number; transparent: boolean };
  spriteMaterial: { opacity: number };
  baseOpacity: number;
};

const linkEndpointId = (endpoint: LinkEndpoint): string =>
  typeof endpoint === "object" && endpoint !== null
    ? endpoint.id
    : String(endpoint ?? "");

/** Library accessors receive their loose NodeObject type; our nodes carry the
 * payload fields we set on them. */
const asNode3d = (node: NodeObject) => node as unknown as Node3D;

const asLink3d = (link: unknown) => link as Link3D;

export function KnowledgeGraphPanel({
  reference,
  focusSlug,
}: {
  reference: string;
  focusSlug?: string | null;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const graph2dRef = useRef<Graph | null>(null);
  const graph3dRef = useRef<ForceGraph3DInstance | null>(null);
  const clickTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const selectedRef = useRef<string | null>(null);
  const first2dRenderRef = useRef(true);
  const showArrowsRef = useRef(true);
  const highlightRef = useRef<{ node: string | null; neighbors: Set<string> }>({
    node: null,
    neighbors: new Set(),
  });
  const node3dStoreRef = useRef(new Map<string, Node3D>());
  const nodeVisualsRef = useRef(new Map<string, NodeVisual>());
  const [mode, setMode] = useState<ViewMode>("2d");
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

  useEffect(() => {
    const saved = window.localStorage.getItem(MODE_STORAGE_KEY);
    if (saved === "2d" || saved === "3d") setMode(saved);
  }, []);

  const changeMode = useCallback((next: ViewMode) => {
    setMode(next);
    window.localStorage.setItem(MODE_STORAGE_KEY, next);
  }, []);

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
    const instance3d = graph3dRef.current;
    if (mode === "3d") {
      instance3d?.zoomToFit(600, 60);
      return;
    }
    const instance = graph2dRef.current;
    void instance?.fitView({ when: "always" }, { duration: 320, easing: "ease-in-out" });
  }, [mode]);

  useEffect(() => {
    setGraphReady(Boolean(visible));
  }, [visible]);

  // The card "⋯" style disambiguation is shared by both engines: a second
  // click on the same node within DBLCLICK_MS expands instead of opening.
  const scheduleOpen = useCallback(
    (slug: string) => {
      if (clickTimer.current) clearTimeout(clickTimer.current);
      clickTimer.current = setTimeout(() => openPage(slug), CLICK_DELAY_MS);
    },
    [openPage],
  );

  const cancelPendingOpen = useCallback(() => {
    if (clickTimer.current) {
      clearTimeout(clickTimer.current);
      clickTimer.current = null;
    }
  }, []);

  // ---------------------------------------------------------------- 2D (G6)

  // The G6 instance is created once per mount; data updates flow through
  // setData so filters and expansions animate instead of rebuilding.
  useEffect(() => {
    if (mode !== "2d" || !graphReady || !containerRef.current || graph2dRef.current) return;
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
      graph2dRef.current = instance;
      instance.on("node:click", (event) => {
        const target = (event as { target?: { id?: string | number } }).target;
        if (!target?.id) return;
        scheduleOpen(String(target.id));
      });
      instance.on("node:dblclick", (event) => {
        const target = (event as { target?: { id?: string | number } }).target;
        if (!target?.id) return;
        cancelPendingOpen();
        expand(String(target.id));
      });
      await instance.render();
      if (disposed) return;
      setGraphVersion((version) => version + 1);
    };

    void draw();
    return () => {
      disposed = true;
      cancelPendingOpen();
      const instance = graph2dRef.current;
      instance?.destroy?.();
      graph2dRef.current = null;
      selectedRef.current = null;
      first2dRenderRef.current = true;
    };
  }, [mode, graphReady, expand, openPage, scheduleOpen, cancelPendingOpen]);

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
    if (mode !== "2d") return;
    const instance = graph2dRef.current;
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
      if (first2dRenderRef.current) {
        first2dRenderRef.current = false;
        void instance.fitView({ when: "always" });
      }
      if (focusSlug && shown.nodes.some((node) => node.slug === focusSlug)) {
        markSelected(instance, focusSlug);
        void instance.focusElement(focusSlug, { duration: 380, easing: "ease-out" });
      }
    });
  }, [mode, shown, focusSlug, graphVersion, degreeOf, markSelected]);

  // Arrow toggles restyle existing edges in place; no re-layout, no reshuffle.
  useEffect(() => {
    showArrowsRef.current = showArrows;
    if (mode === "3d") {
      // The 3D engine expresses the same switch as flowing particles.
      const instance = graph3dRef.current;
      if (instance) instance.linkDirectionalParticles(instance.linkDirectionalParticles());
      return;
    }
    const instance = graph2dRef.current;
    if (!instance) return;
    const edges = instance.getEdgeData();
    if (edges.length === 0) return;
    instance.updateEdgeData(
      edges.map((edge) => ({ id: edge.id, data: { endArrow: showArrows } })),
    );
    void instance.render();
  }, [showArrows, mode]);

  // ---------------------------------------------------------------- 3D

  useEffect(() => {
    if (mode !== "3d" || !graphReady || !containerRef.current || graph3dRef.current) return;
    let disposed = false;
    let detachResize: (() => void) | null = null;
    let detachClick: (() => void) | null = null;
    const container = containerRef.current;

    const draw = async () => {
      const [{ default: ForceGraph3D }, THREE, { default: SpriteText }, { UnrealBloomPass }] =
        await Promise.all([
          import("3d-force-graph"),
          import("three"),
          import("three-spritetext"),
          import("three/examples/jsm/postprocessing/UnrealBloomPass.js"),
        ]);
      if (disposed) return;

      const buildNodeObject = (libNode: NodeObject) => {
        const node = asNode3d(libNode);
        const color = TYPE_COLORS[node.pageType] ?? TYPE_COLORS.page;
        const radius = 2.7 * Math.sqrt(node.val);
        const material = new THREE.MeshBasicMaterial({
          color,
          transparent: true,
          opacity: 0.95,
        });
        const mesh = new THREE.Mesh(new THREE.SphereGeometry(radius, 24, 24), material);
        const sprite = new SpriteText(node.title);
        sprite.color = "#d6d6d6";
        sprite.backgroundColor = "rgb(18 18 18 / 72%)";
        sprite.padding = 0.5;
        sprite.textHeight = 3.6;
        sprite.position.set(0, -(radius + 3.2), 0);
        const group = new THREE.Group();
        group.add(mesh);
        group.add(sprite);
        nodeVisualsRef.current.set(node.id, {
          material,
          spriteMaterial: sprite.material,
          baseOpacity: 0.95,
        });
        return group;
      };

      // three-render-objects defaults its viewport to the window size; pin it
      // to the container or the graph renders offset from the visible pane.
      const instance = new ForceGraph3D(container, {
        controlType: "orbit",
        rendererConfig: { antialias: true, alpha: true },
      })
        .width(container.clientWidth)
        .height(container.clientHeight || 560)
        .backgroundColor("rgba(0,0,0,0)")
        .showNavInfo(false)
        .nodeLabel((node) => asNode3d(node).title)
        .nodeVal((node) => asNode3d(node).val)
        .nodeThreeObject(buildNodeObject)
        .linkOpacity(0.24)
        .linkColor((rawLink) => {
          const link = asLink3d(rawLink);
          const { node, neighbors } = highlightRef.current;
          if (!node) return "#3f3f3f";
          const source = linkEndpointId(link.source);
          const target = linkEndpointId(link.target);
          return source === node || target === node ? "#b9b9b9" : "rgb(80 80 80 / 10%)";
        })
        .linkWidth((rawLink) => {
          const link = asLink3d(rawLink);
          const { node } = highlightRef.current;
          if (!node) return 0;
          const source = linkEndpointId(link.source);
          const target = linkEndpointId(link.target);
          return source === node || target === node ? 1.4 : 0;
        })
        .linkDirectionalParticles((rawLink) => {
          const link = asLink3d(rawLink);
          if (!showArrowsRef.current) return 0;
          const { node } = highlightRef.current;
          if (!node) return 2;
          const source = linkEndpointId(link.source);
          const target = linkEndpointId(link.target);
          return source === node || target === node ? 5 : 1;
        })
        .linkDirectionalParticleWidth(2)
        .linkDirectionalParticleSpeed(0.0055)
        .linkDirectionalParticleColor((rawLink) => {
          const link = asLink3d(rawLink);
          const { node } = highlightRef.current;
          if (!node) return "#8a8a8a";
          const source = linkEndpointId(link.source);
          const target = linkEndpointId(link.target);
          return source === node || target === node ? "#e6e6e6" : "#4a4a4a";
        })
        .onEngineStop(() => {
          if (!disposed) instance.zoomToFit(800, 60);
        });
      // Engine-stop timing varies; guarantee an initial frame with fallbacks.
      [1400, 3600].forEach((delay) => {
        setTimeout(() => {
          if (!disposed && graph3dRef.current === instance) instance.zoomToFit(700, 60);
        }, delay);
      });

      // Bloom: bright node colours and particles glow over the dark theme.
      instance.postProcessingComposer().addPass(
        new UnrealBloomPass(
          new THREE.Vector2(container.clientWidth, container.clientHeight || 560),
          0.85,
          0.55,
          0.14,
        ),
      );

      // A gentler repulsion keeps degree-0 nodes (the index page) close to
      // the cluster instead of drifting far away and skewing zoomToFit.
      const charge = (
        instance as unknown as {
          d3Force?: (name: string) => { strength: (value: number) => void } | undefined;
        }
      ).d3Force?.("charge");
      charge?.strength(-220);

      // Ambient dust gives the empty space a sense of depth.
      const dustCount = 320;
      const dustPositions = new Float32Array(dustCount * 3);
      for (let index = 0; index < dustCount; index += 1) {
        dustPositions[index * 3] = (Math.random() - 0.5) * 1400;
        dustPositions[index * 3 + 1] = (Math.random() - 0.5) * 1400;
        dustPositions[index * 3 + 2] = (Math.random() - 0.5) * 1400;
      }
      const dustGeometry = new THREE.BufferGeometry();
      dustGeometry.setAttribute(
        "position",
        new THREE.Float32BufferAttribute(dustPositions, 3),
      );
      const dust = new THREE.Points(
        dustGeometry,
        new THREE.PointsMaterial({ color: "#3a3a3a", size: 1.6, sizeAttenuation: true }),
      );
      instance.scene().add(dust);

      // Clicks go through a native listener with a screen-space hit test:
      // nearest node centre within tolerance, which stays dependable across
      // zoom levels instead of relying on the library's internal raycast
      // pipeline for the press/release pair.
      let lastClick: { id: string | null; time: number } = { id: null, time: 0 };
      const pickNode = (clientX: number, clientY: number) => {
        const rect = container.getBoundingClientRect();
        let best: Node3D | null = null;
        let bestDistance = 26;
        for (const node of node3dStoreRef.current.values()) {
          if (node.x === undefined) continue;
          const screen = instance.graph2ScreenCoords(node.x, node.y ?? 0, node.z ?? 0);
          const distance = Math.hypot(rect.left + screen.x - clientX, rect.top + screen.y - clientY);
          if (distance < bestDistance) {
            bestDistance = distance;
            best = node;
          }
        }
        return best;
      };
      const onNativeClick = (ev: MouseEvent) => {
        const node = pickNode(ev.clientX, ev.clientY);
        if (!node) return;
        const now = Date.now();
        if (lastClick.id === node.id && now - lastClick.time < DBLCLICK_MS) {
          lastClick = { id: null, time: 0 };
          cancelPendingOpen();
          expand(node.id);
          const current = instance.cameraPosition();
          const distance = Math.hypot(
            current.x - (node.x ?? 0),
            current.y - (node.y ?? 0),
            current.z - (node.z ?? 0),
          );
          const targetPosition = { x: node.x ?? 0, y: node.y ?? 0, z: node.z ?? 0 };
          instance.cameraPosition(
            { x: targetPosition.x, y: targetPosition.y, z: targetPosition.z + distance * 0.75 },
            targetPosition,
            600,
          );
          return;
        }
        lastClick = { id: node.id, time: now };
        scheduleOpen(node.id);
      };
      container.addEventListener("click", onNativeClick);
      detachClick = () => container.removeEventListener("click", onNativeClick);
      instance.onNodeHover((node) => {
        const slug = node ? String(node.id) : null;
        highlightRef.current = {
          node: slug,
          neighbors: slug ? new Set(adjacency.get(slug) ?? []) : new Set(),
        };
        const highlight = highlightRef.current;
        for (const [id, visual] of nodeVisualsRef.current) {
          const active = !highlight.node || id === highlight.node || highlight.neighbors.has(id);
          visual.material.opacity = active ? visual.baseOpacity : 0.12;
          visual.spriteMaterial.opacity = active ? 1 : 0.12;
        }
        // Re-apply the accessors so link colors/particles re-evaluate.
        instance.linkColor(instance.linkColor());
        instance.linkWidth(instance.linkWidth());
        instance.linkDirectionalParticles(instance.linkDirectionalParticles());
      });

      const onResize = () => {
        if (disposed) return;
        instance.width(container.clientWidth).height(container.clientHeight || 560);
      };
      window.addEventListener("resize", onResize);
      detachResize = () => window.removeEventListener("resize", onResize);

      graph3dRef.current = instance;
      if (!disposed) setGraphVersion((version) => version + 1);
    };

    void draw();
    return () => {
      disposed = true;
      detachResize?.();
      detachClick?.();
      cancelPendingOpen();
      const instance = graph3dRef.current;
      instance?._destructor();
      graph3dRef.current = null;
      nodeVisualsRef.current.clear();
      highlightRef.current = { node: null, neighbors: new Set() };
    };
  }, [mode, graphReady, adjacency, expand, scheduleOpen, cancelPendingOpen]);

  useEffect(() => {
    if (mode !== "3d") return;
    const instance = graph3dRef.current;
    if (!instance || !shown) return;
    // Reusing the same node objects preserves simulation positions, so
    // expansions keep the existing constellation in place.
    const store = node3dStoreRef.current;
    const keep = new Set<string>();
    const nodes = shown.nodes.map((node) => {
      keep.add(node.slug);
      const existing = store.get(node.slug);
      if (existing) {
        existing.title = node.title;
        existing.pageType = node.pageType;
        existing.val = 1 + Math.min(degreeOf.get(node.slug) ?? 0, 6) + (node.pageType === "summary" ? 2 : 0);
        return existing;
      }
      const fresh: Node3D = {
        id: node.slug,
        title: node.title,
        pageType: node.pageType,
        val: 1 + Math.min(degreeOf.get(node.slug) ?? 0, 6) + (node.pageType === "summary" ? 2 : 0),
        x: (Math.random() - 0.5) * 60,
        y: (Math.random() - 0.5) * 60,
        z: (Math.random() - 0.5) * 60,
      };
      store.set(node.slug, fresh);
      return fresh;
    });
    for (const slug of [...store.keys()]) {
      if (!keep.has(slug)) store.delete(slug);
    }
    for (const slug of [...nodeVisualsRef.current.keys()]) {
      if (!keep.has(slug)) nodeVisualsRef.current.delete(slug);
    }
    instance.graphData({
      nodes,
      links: shown.links.map(([source, target]) => ({ source, target })),
    });
    if (focusSlug && keep.has(focusSlug)) {
      const node = store.get(focusSlug);
      if (node && node.x !== undefined) {
        const current = instance.cameraPosition();
        const distance = Math.hypot(
          current.x - node.x,
          current.y - (node.y ?? 0),
          current.z - (node.z ?? 0),
        );
        const targetPosition = { x: node.x, y: node.y ?? 0, z: node.z ?? 0 };
        instance.cameraPosition(
          { x: targetPosition.x, y: targetPosition.y, z: targetPosition.z + Math.max(distance, 120) * 0.8 },
          targetPosition,
          700,
        );
      }
    }
  }, [mode, shown, graphVersion, degreeOf, focusSlug]);

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
          <span className={styles.hint}>
            {mode === "3d"
              ? "单击打开页面 · 双击展开邻居 · 拖拽旋转 · 滚轮缩放"
              : "单击打开页面 · 双击展开邻居"}
          </span>
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

          <button
            type="button"
            className={styles.panelAction}
            onClick={() => changeMode(mode === "3d" ? "2d" : "3d")}
          >
            <span aria-hidden="true">{mode === "3d" ? "◻" : "✦"}</span>
            {mode === "3d" ? "切换 2D 视图" : "切换 3D 视图"}
          </button>
          <button type="button" className={styles.panelAction} onClick={fitView}>
            <span aria-hidden="true">⤢</span> 适应屏幕
          </button>
          <button
            type="button"
            className={styles.panelAction}
            onClick={() => setShowArrows((current) => !current)}
          >
            <span aria-hidden="true">↗</span>
            {mode === "3d"
              ? showArrows
                ? "隐藏粒子流"
                : "显示粒子流"
              : showArrows
                ? "隐藏箭头"
                : "显示箭头"}
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
