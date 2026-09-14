// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  getWikiGraph: vi.fn(),
  getWikiPage: vi.fn(),
  listKnowledgeBases: vi.fn(),
}));
vi.mock("../src/lib/studio-client", () => ({ studioClient: api }));

/** G6 stand-in: keeps the node handlers the panel binds (so a test can drive the
 * click / double-click pair itself) and every setData payload (so a test can see
 * which nodes survived an expansion). */
const g6 = vi.hoisted(() => {
  class FakeGraph {
    static instances: FakeGraph[] = [];
    handlers = new Map<string, (event: unknown) => void>();
    nodes: string[] = [];
    fits = 0;
    focused: string[] = [];
    constructor() {
      FakeGraph.instances.push(this);
    }
    on(type: string, handler: (event: unknown) => void) {
      this.handlers.set(type, handler);
    }
    async render() {}
    setData(data: { nodes: Array<{ id: string }> }) {
      this.nodes = data.nodes.map((node) => node.id);
    }
    getElementPosition() {
      return [0, 0];
    }
    async fitView() {
      this.fits += 1;
    }
    async focusElement(slug: string) {
      this.focused.push(slug);
    }
    async setElementState() {}
    getEdgeData() {
      return [];
    }
    async updateEdgeData() {}
    destroy() {}
  }
  return { FakeGraph };
});
vi.mock("@antv/g6", () => ({ Graph: g6.FakeGraph }));

/** 3D stand-in: the panel drives this engine through chained setters, keeps the
 * graph data it is handed, and — like the real library — builds a node object
 * per node, which exercises the shipped node geometry. */
const engine = vi.hoisted(() => {
  type Node3D = { id: string; x?: number; y?: number; z?: number };
  class Fake3D {
    static instances: Fake3D[] = [];
    container: HTMLElement;
    nodes: Node3D[] = [];
    cameraCalls: Array<{ position: unknown; lookAt: unknown; ms: number }> = [];
    position = { x: 0, y: 0, z: 300 };
    fits = 0;
    private stopHandler: (() => void) | null = null;
    private nodeObject: ((node: Node3D) => unknown) | null = null;
    constructor(container: HTMLElement) {
      this.container = container;
      Fake3D.instances.push(this);
    }
    graphData(data: { nodes: Node3D[] }) {
      this.nodes = data.nodes;
      for (const node of data.nodes) this.nodeObject?.(node);
      return this;
    }
    // Reading (no argument) returns the current camera position; writing
    // records the framing so a test can assert what the panel aimed at.
    cameraPosition(position?: unknown, lookAt?: unknown, ms = 0) {
      if (position === undefined) return { ...this.position };
      this.position = position as { x: number; y: number; z: number };
      this.cameraCalls.push({ position, lookAt, ms });
      return this;
    }
    zoomToFit() {
      this.fits += 1;
      return this;
    }
    onEngineStop(handler: () => void) {
      this.stopHandler = handler;
      return this;
    }
    /** Test hook: what the layout engine does when the simulation settles. */
    settle() {
      this.stopHandler?.();
    }
    nodeThreeObject(accessor: (node: Node3D) => unknown) {
      this.nodeObject = accessor;
      return this;
    }
    postProcessingComposer() {
      return { addPass() {} };
    }
    d3Force() {
      return { strength() {} };
    }
    scene() {
      return { add() {} };
    }
    controls() {
      return { addEventListener() {} };
    }
    graph2ScreenCoords(x: number, y: number) {
      // Offsets so the click maths below has to go through the hit test.
      return { x: x + 500, y: y + 500 };
    }
    width() {
      return this;
    }
    height() {
      return this;
    }
    _destructor() {}
  }
  // Chained style setters, all no-ops that stay chainable.
  for (const name of [
    "backgroundColor",
    "showNavInfo",
    "nodeLabel",
    "nodeVal",
    "linkOpacity",
    "linkColor",
    "linkWidth",
    "linkDirectionalParticles",
    "linkDirectionalParticleWidth",
    "linkDirectionalParticleSpeed",
    "linkDirectionalParticleColor",
    "onNodeHover",
  ]) {
    Object.defineProperty(Fake3D.prototype, name, {
      value: function chainable(this: Fake3D) {
        return this;
      },
    });
  }
  return { Fake3D };
});
vi.mock("3d-force-graph", () => ({ default: engine.Fake3D }));
vi.mock("three-spritetext", () => ({
  default: class {
    material = { opacity: 1 };
    position = { set() {} };
    constructor(_text: string) {}
  },
}));
vi.mock("three/examples/jsm/postprocessing/UnrealBloomPass.js", () => ({
  UnrealBloomPass: class {},
}));
vi.mock("three", () => {
  class Object3D {
    children: unknown[] = [];
    material: unknown;
    position = { set() {} };
    constructor(material?: unknown) {
      this.material = material;
    }
    add(...items: unknown[]) {
      this.children.push(...items);
    }
  }
  return {
    MeshBasicMaterial: class {},
    SphereGeometry: class {},
    Mesh: Object3D,
    Group: Object3D,
    Vector2: class {},
    BufferGeometry: class {
      setAttribute() {}
    },
    Float32BufferAttribute: class {},
    Points: Object3D,
    PointsMaterial: class {},
  };
});

import { KnowledgeGraphPanel } from "../src/components/knowledge/knowledge-graph-panel";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

// a–b–c: expanding a reveals b only, so the surviving node set proves the expand.
const graph = {
  nodes: [
    { slug: "a", title: "甲", pageType: "entity", linkCount: 1 },
    { slug: "b", title: "乙", pageType: "entity", linkCount: 2 },
    { slug: "c", title: "丙", pageType: "entity", linkCount: 1 },
  ],
  links: [["a", "b"], ["b", "c"]] as Array<[string, string]>,
};

let host: HTMLDivElement;
let root: Root;
let stored: Map<string, string>;

const instance = () => g6.FakeGraph.instances[0];
const engine3d = () => engine.Fake3D.instances[0];
const drawer = () => document.querySelector('[role="dialog"]');
const ids = (nodes: Array<{ id: string }>) => nodes.map((node) => node.id);
const click = (slug: string) =>
  act(() => {
    instance().handlers.get("node:click")?.({ target: { id: slug } });
  });
const dblclick = (slug: string) =>
  act(() => {
    instance().handlers.get("node:dblclick")?.({ target: { id: slug } });
  });
const wait = (ms: number) => act(async () => void vi.advanceTimersByTime(ms));

/** Clicks land on the real container: the 3D engine reads them through a
 * screen-space hit test rather than through library events. */
function clickAt3d(slug: string, type = "click") {
  const node = engine3d().nodes.find((candidate) => candidate.id === slug);
  const screen = engine3d().graph2ScreenCoords(node?.x ?? 0, node?.y ?? 0);
  act(() => {
    engine3d().container.dispatchEvent(
      new MouseEvent(type, { clientX: screen.x, clientY: screen.y, bubbles: true }),
    );
  });
}

async function mount(mode: "2d" | "3d" = "2d", focusSlug?: string) {
  stored.set("knowledge-graph-view-mode", mode);
  await act(async () => {
    root.render(<KnowledgeGraphPanel reference="cases" focusSlug={focusSlug} />);
  });
  // load() resolves, the engine is built, renders, and re-syncs its data.
  for (let index = 0; index < 4; index += 1) {
    await act(async () => {
      await Promise.resolve();
    });
  }
}

beforeEach(() => {
  vi.useFakeTimers();
  stored = new Map();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => stored.get(key) ?? null,
    setItem: (key: string, value: string) => stored.set(key, value),
  });
  api.getWikiGraph.mockResolvedValue(graph);
  api.getWikiPage.mockResolvedValue({
    title: "甲",
    content: "正文",
    slug: "a",
    pageType: "entity",
    categoryPath: [],
    summary: "",
  });
  api.listKnowledgeBases.mockResolvedValue([]);
  g6.FakeGraph.instances.length = 0;
  engine.Fake3D.instances.length = 0;
  host = document.createElement("div");
  document.body.append(host);
  root = createRoot(host);
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
  vi.useRealTimers();
  vi.resetAllMocks();
  vi.unstubAllGlobals();
  document.body.innerHTML = "";
});

it("opens the page drawer on a single click, held past the double-click window", async () => {
  await mount();
  click("a");
  expect(drawer()).toBeNull();
  await wait(240);
  expect(drawer()?.getAttribute("aria-label")).toBe("Wiki 页面详情");
});

it("expands on a double click without leaving the drawer open", async () => {
  await mount();
  click("a");
  await wait(120);
  click("a");
  await wait(1000);
  expect(drawer()).toBeNull();
  expect(instance().nodes).toEqual(["a", "b"]);
});

it("takes back the drawer a slow second click had already opened", async () => {
  await mount();
  click("a");
  await wait(240);
  expect(drawer()).not.toBeNull();
  // Second click lands after the hold-off fired but inside the expand window.
  click("a");
  await wait(1000);
  expect(drawer()).toBeNull();
  expect(instance().nodes).toEqual(["a", "b"]);
});

it("treats the engine's own double click as the expand gesture", async () => {
  await mount();
  click("a");
  await wait(540);
  expect(drawer()).not.toBeNull();
  dblclick("a");
  await wait(1000);
  expect(drawer()).toBeNull();
  expect(instance().nodes).toEqual(["a", "b"]);
});

it("frames a jumped-to node instead of fitting the whole graph", async () => {
  await mount("2d", "b");
  expect(instance().focused).toEqual(["b"]);
  expect(instance().fits).toBe(0);
});

it("still fits the whole graph when the view opens without a target", async () => {
  await mount();
  expect(instance().fits).toBe(1);
  expect(instance().focused).toEqual([]);
});

describe("3D engine", () => {
  it("expands on a double click without leaving the drawer open", async () => {
    await mount("3d");
    clickAt3d("a");
    await wait(120);
    clickAt3d("a");
    await wait(1000);
    expect(drawer()).toBeNull();
    expect(ids(engine3d().nodes)).toEqual(["a", "b"]);
  });

  it("treats the browser's own double click as the expand gesture", async () => {
    await mount("3d");
    clickAt3d("a");
    await wait(540);
    expect(drawer()).not.toBeNull();
    clickAt3d("a", "dblclick");
    await wait(1000);
    expect(drawer()).toBeNull();
    expect(ids(engine3d().nodes)).toEqual(["a", "b"]);
  });

  it("holds the camera on a jumped-to node instead of fitting", async () => {
    await mount("3d", "b");
    expect(engine3d().cameraCalls).toHaveLength(1);
    // The fallback fits run at 1.4s and 3.6s; the anchor must outlast them.
    await wait(4000);
    expect(engine3d().fits).toBe(0);
  });

  it("re-aims at the settled node when the engine stops, and fits otherwise", async () => {
    await mount("3d", "b");
    await wait(3600);
    act(() => engine3d().settle());
    expect(engine3d().cameraCalls.length).toBeGreaterThan(1);
    expect(engine3d().fits).toBe(0);

    act(() => root.unmount());
    engine.Fake3D.instances.length = 0;
    host.remove();
    host = document.createElement("div");
    document.body.append(host);
    root = createRoot(host);
    await mount("3d");
    await wait(4000);
    expect(engine3d().fits).toBeGreaterThan(0);
  });
});
