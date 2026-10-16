// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it } from "vitest";
import { PresentationMenu } from "../src/components/intelligent-ui/presentation-menu";
import type { Presentation } from "../src/components/intelligent-ui/schema";
(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;
let host: HTMLDivElement, root: Root;
const presentation: Presentation = {
  schema_version: "harness.ui/v1", presentation_id: "ui_owned", revision: 3,
  run_id: "run-1", message_id: "message-1", placement: "inline", kind: "html",
  renderer_version: "html/1", fallback_text: "按人数调整采购清单", data: { artifact_id: "artifact-1", name: "map.html" },
  config: {}, actions: [], decision: { origin: "model" }, source_refs: [], snapshot_hash: "abcdef",
};
beforeEach(() => {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  act(() => root.render(<PresentationMenu presentation={presentation} />));
});
afterEach(() => { act(() => root.unmount()); host.remove(); });
it("opens the owning revision in an isolated tab without a hidden menu", () => {
  expect(host.querySelector("button")).toBeNull();
  const link = host.querySelector<HTMLAnchorElement>('[aria-label="在新标签页打开"]')!;
  expect(link.getAttribute("href")).toBe("/api/ui/presentations/ui_owned/html?revision=3");
  expect(link.target).toBe("_blank");
  expect(link.rel).toBe("noopener noreferrer");
});
it("downloads the published artifact through the authenticated attachment route", () => {
  const link = host.querySelector<HTMLAnchorElement>('[aria-label="下载 HTML"]')!;
  expect(link.getAttribute("href")).toBe("/api/harness/artifacts/artifact-1");
  expect(link.download).toBe("map.html");
  expect(link.hasAttribute("target")).toBe(false);
  expect(host.textContent).not.toContain("abcdef");
});
