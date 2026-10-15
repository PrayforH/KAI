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
  renderer_version: "html/1", fallback_text: "按人数调整采购清单", data: { artifact_id: "artifact-1" },
  config: {}, actions: [], decision: { origin: "model" }, source_refs: [], snapshot_hash: "abcdef",
};
beforeEach(() => {
  host = document.createElement("div"); document.body.append(host); root = createRoot(host);
  act(() => root.render(<PresentationMenu presentation={presentation} />));
});
afterEach(() => { act(() => root.unmount()); host.remove(); });
const trigger = () => host.querySelector("button")!;
const panel = () => document.querySelector('[role="dialog"][aria-label="工具信息"]')!;
it("keeps secondary metadata out of the card until opened, with the owning document revision", () => {
  expect(document.querySelector('[aria-label="工具信息"]')).toBeNull();
  expect(host.querySelector("a")).toBeNull();
  act(() => trigger().click());
  expect(panel().textContent).toContain("按人数调整采购清单");
  const link = panel().querySelector("a")!;
  expect(link.getAttribute("href")).toBe("/api/ui/presentations/ui_owned/html?revision=3");
  expect(link.rel).toBe("noopener noreferrer");
  act(() => link.click());
  expect(document.querySelector('[aria-label="工具信息"]')).toBeNull();
});
it("supports keyboard entry and Escape returns focus to its trigger", () => {
  act(() => trigger().dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true })));
  expect(document.activeElement).toBe(panel().querySelector("a"));
  act(() => document.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true })));
  expect(trigger().getAttribute("aria-expanded")).toBe("false");
  expect(document.activeElement).toBe(trigger());
});
it("keeps internal scrolling open and dismisses outside clicks", () => {
  act(() => trigger().click());
  act(() => panel().dispatchEvent(new Event("scroll", { bubbles: true })));
  expect(trigger().getAttribute("aria-expanded")).toBe("true");
  act(() => document.body.dispatchEvent(new MouseEvent("pointerdown", { bubbles: true })));
  expect(trigger().getAttribute("aria-expanded")).toBe("false");
});
