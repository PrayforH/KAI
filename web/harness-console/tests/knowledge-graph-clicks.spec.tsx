// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

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

const instance = () => g6.FakeGraph.instances[0];
const drawer = () => document.querySelector('[role="dialog"]');
const click = (slug: string) =>
  act(() => {
    instance().handlers.get("node:click")?.({ target: { id: slug } });
  });
const dblclick = (slug: string) =>
  act(() => {
    instance().handlers.get("node:dblclick")?.({ target: { id: slug } });
  });
const wait = (ms: number) => act(async () => void vi.advanceTimersByTime(ms));

async function mount(focusSlug?: string) {
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
  const stored = new Map<string, string>();
  stored.set("knowledge-graph-view-mode", "2d");
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
  await mount("b");
  expect(instance().focused).toEqual(["b"]);
  expect(instance().fits).toBe(0);
});

it("still fits the whole graph when the view opens without a target", async () => {
  await mount();
  expect(instance().fits).toBe(1);
  expect(instance().focused).toEqual([]);
});
