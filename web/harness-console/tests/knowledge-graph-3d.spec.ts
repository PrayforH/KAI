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
    expect(component).toContain(".onEngineStop(autoFit)");
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
    expect(component).toContain("if (!disposed && !userNavigatedRef.current)");
  });

  it("gives node types faceted model silhouettes with glow shells", () => {
    expect(component).toContain("IcosahedronGeometry");
    expect(component).toContain("OctahedronGeometry");
    expect(component).toContain("DodecahedronGeometry");
    expect(component).toContain("MeshStandardMaterial");
    expect(component).toContain("emissiveIntensity");
    expect(component).toContain("wireframe: true");
  });
});
