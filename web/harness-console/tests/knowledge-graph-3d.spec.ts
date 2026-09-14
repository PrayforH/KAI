import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const component = readFileSync(
  join(process.cwd(), "src/components/knowledge/knowledge-graph-panel.tsx"),
  "utf8",
);

describe("Knowledge graph 3D view", () => {
  it("toggles between the G6 and 3D engines and remembers the choice", () => {
    expect(component).toContain('MODE_STORAGE_KEY = "knowledge-graph-view-mode"');
    expect(component).toContain("localStorage.setItem(MODE_STORAGE_KEY");
    expect(component).toContain("切换 3D 视图");
    expect(component).toContain("切换 2D 视图");
  });

  it("lazy-loads the 3D stack only in 3D mode", () => {
    expect(component).toContain('import("3d-force-graph")');
    expect(component).toContain('import("three-spritetext")');
    expect(component).toContain('import("three/examples/jsm/postprocessing/UnrealBloomPass.js")');
    expect(component).toContain('if (mode !== "3d" || !graphReady');
    expect(component).toContain('if (mode !== "2d" || !graphReady');
  });

  it("applies the signature effects: bloom, flow particles, dust, hover dimming", () => {
    expect(component).toContain("UnrealBloomPass");
    expect(component).toContain("linkDirectionalParticleSpeed");
    expect(component).toContain("linkDirectionalParticleColor");
    expect(component).toContain("PointsMaterial");
    expect(component).toContain("onNodeHover");
    expect(component).toContain("material.opacity = active ? visual.baseOpacity : 0.12");
  });

  it("keeps single-click drawer and double-click expand semantics in 3D", () => {
    expect(component).toContain("DBLCLICK_MS");
    expect(component).toContain("cancelPendingOpen()");
    expect(component).toContain("instance.cameraPosition(");
    expect(component).toContain(".onEngineStop(() => {");
  });

  it("reuses node objects across data updates so positions survive expansion", () => {
    expect(component).toContain("node3dStoreRef");
    expect(component).toContain("const existing = store.get(node.slug);");
  });

  it("picks clicked nodes in screen space via a native container listener", () => {
    expect(component).toContain('container.addEventListener("click", onNativeClick)');
    expect(component).toContain("graph2ScreenCoords");
    expect(component).not.toContain("window.__kaiGraph3d");
  });

  it("guarantees an initial fit independent of engine-stop timing", () => {
    expect(component).toContain("[1400, 3600].forEach");
    expect(component).toContain("graph3dRef.current?.zoomToFit(700, 60)");
  });

  it("stands down every auto-fit once the user navigates the camera", () => {
    expect(component).toContain("userNavigatedRef");
    expect(component).toContain('container.addEventListener("wheel", markNavigated');
    expect(component).toContain('addEventListener?.("start", markNavigated)');
    expect(component).toContain("if (!disposed && !userNavigatedRef.current && !focusHoldRef.current)");
  });

  it("renders nodes as glowing spheres (polyhedra experiment reverted)", () => {
    expect(component).toContain("SphereGeometry(radius, 24, 24)");
    expect(component).toContain("MeshBasicMaterial");
    expect(component).not.toContain("IcosahedronGeometry");
    expect(component).not.toContain("wireframe: true");
  });

  it("reframes the whole revealed neighbourhood after an expansion", () => {
    expect(component).toContain("pendingFitRef.current = true");
    expect(component).toContain("setTimeout(fitReframed, 500)");
    expect(component).toContain("setTimeout(fitReframed, 1800)");
  });
});

describe("Jumping into the graph keeps the target framed", () => {
  it("skips the 2D whole-graph fit when the jump carries a target", () => {
    // fitView (G6 default 500ms) and focusElement (380ms) animate the same
    // viewport from one tick, so the fit used to deliver the final frame.
    expect(component).toContain("if (!focusTarget) void instance.fitView({ when: \"always\" })");
    expect(component).toContain("void instance.focusElement(focusTarget, { duration: 380");
    expect(component).toContain("focusHoldRef.current === focusTarget) return;");
  });

  it("stands the 3D auto-fits down while a jumped-to node is anchored", () => {
    expect(component).toContain("const focusHoldRef = useRef<string | null>(null)");
    expect(component).toContain("!userNavigatedRef.current && !focusHoldRef.current");
    expect(component).toContain("focusHoldRef.current !== focusSlug)");
    expect(component).toContain("if (flyToNode3d(focusSlug, 700)) focusHoldRef.current = focusSlug;");
    // The engine keeps moving nodes after arrival, so the stop handler re-aims
    // at the settled position instead of fitting the whole graph.
    expect(component).toContain("flyToNode3d(focusHoldRef.current, 600);");
  });

  it("releases the anchor when the user takes over the viewport", () => {
    expect(component).toContain('container.addEventListener("wheel", releaseFocusHold');
    expect(component).toContain('container.addEventListener("wheel", markNavigated');
    expect(component).toContain("focusHoldRef.current = null;");
  });
});

describe("Single click and double click stay unambiguous", () => {
  it("resolves both gestures through one shared click path in either engine", () => {
    expect(component).toContain("const resolveNodeClick = useCallback(");
    expect(component).toContain("if (last.id === slug && now - last.time < DBLCLICK_MS)");
    expect(component).toContain("if (resolveNodeClick(slug)) expand(slug);");
    expect(component).toContain("if (resolveNodeClick(node.id)) expandFromGesture(node.id);");
  });

  it("takes back a drawer the delayed single click already opened", () => {
    // A double click slower than the hold-off would otherwise leave the page
    // drawer open underneath the expand gesture.
    expect(component).toContain("openedByClickRef");
    expect(component).toContain("const undoClickOpen = useCallback(");
    expect(component).toContain("if (openedByClickRef.current !== slug) return;");
    expect(component).toContain("setPageSlug((current) => (current === slug ? null : current))");
  });

  it("covers slow pairs with the browser's own double click", () => {
    expect(component).toContain('instance.on("node:dblclick"');
    expect(component).toContain('container.addEventListener("dblclick", onNativeDblClick)');
    expect(component).toContain("undoClickOpen(slug);");
    expect(component).toContain("undoClickOpen(node.id);");
  });
});
