// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { IntelligentUiBlock } from "../src/components/intelligent-ui/presentation-host";
import {
  modelBlockCount,
  type Presentation,
  parsePresentation,
  presentationsForMessage,
  stripHarnessUiFences,
} from "../src/components/intelligent-ui/schema";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

let root: Root | undefined;
let host: HTMLDivElement | undefined;

const block = (overrides: Record<string, unknown> = {}) => ({
  schema_version: "harness.ui/v1",
  presentation_id: "ui_1",
  revision: 1,
  run_id: "run-1",
  message_id: "assistant-run-1",
  placement: "inline",
  kind: "table",
  renderer_version: "table/1",
  title: "季度处理量",
  fallback_text: "第一季度 120 件。",
  data: {
    columns: [
      { key: "quarter", label: "季度" },
      { key: "count", label: "数量" },
    ],
    rows: [
      { quarter: "第一季度", count: 120 },
      { quarter: "第二季度", count: 150 },
    ],
  },
  config: {},
  decision: { origin: "model" },
  ...overrides,
});

/** The fixture is only useful if it is a block the console accepts. */
function presentation(overrides: Record<string, unknown> = {}): Presentation {
  const parsed = parsePresentation(block(overrides));
  if (!parsed) throw new Error("fixture does not parse as a presentation");
  return parsed;
}

beforeEach(() => {
  host = document.createElement("div");
  document.body.append(host);
});

afterEach(() => {
  if (root) act(() => root?.unmount());
  root = undefined;
  host?.remove();
  host = undefined;
});

function render(value: unknown) {
  root = createRoot(host as HTMLDivElement);
  act(() => root?.render(<IntelligentUiBlock value={value} />));
}

it("parses a block the server published", () => {
  const parsed = parsePresentation(presentation());
  expect(parsed?.kind).toBe("table");
  expect(parsed?.presentation_id).toBe("ui_1");
});

it("refuses a block whose renderer version this bundle does not implement", () => {
  expect(parsePresentation(block({ renderer_version: "table/2" }))).toBeNull();
});

it("refuses a block missing its schema version", () => {
  expect(parsePresentation(block({ schema_version: "harness.ui/v2" }))).toBeNull();
});

it("collects the turn's blocks from tool-call parts, newest fields intact", () => {
  const parts = [
    { type: "text" },
    { type: "tool-call", toolName: "harness_run_activity", args: {} },
    { type: "tool-call", toolName: "harness_present_ui", args: presentation() },
    { type: "tool-call", toolName: "harness_present_ui", args: presentation() },
    { type: "tool-call", toolName: "harness_present_ui", args: presentation({ presentation_id: "ui_2" }) },
  ];
  const found = presentationsForMessage(parts);
  expect(found.map((item) => item.presentation_id)).toEqual(["ui_1", "ui_2"]);
});

it("renders a table block with its title and headers", () => {
  render(presentation());
  expect(host?.textContent).toContain("季度处理量");
  expect(host?.textContent).toContain("第二季度");
  expect(host?.querySelector("table")).not.toBeNull();
});

it("falls back to the sentence when the bundle cannot render the block", () => {
  render(block({ renderer_version: "table/2" }));
  expect(host?.textContent).toBe("第一季度 120 件。");
  expect(host?.querySelector("table")).toBeNull();
});

it("renders a metric block", () => {
  render(
    presentation({
      kind: "metric",
      renderer_version: "metric/1",
      title: null,
      data: { items: [{ label: "转化率", value: "18.4", unit: "%" }] },
    }),
  );
  expect(host?.textContent).toContain("转化率");
  expect(host?.textContent).toContain("18.4");
});

it("hides only the fences the server actually published", () => {
  const first = '```harness-ui\n{"version":1,"kind":"table"}\n```';
  const second = '```harness-ui\n{"version":1,"kind":"metric"}\n```';
  const text = `结论。\n\n${first}\n\n${second}`;

  expect(stripHarnessUiFences(text, 2)).toBe("结论。\n\n\n\n");
  // A rejected block stays visible: a hole where the model's content used to be
  // is worse than a code block.
  expect(stripHarnessUiFences(text, 1)).toBe(`结论。\n\n\n\n${second}`);
  expect(stripHarnessUiFences(text, 0)).toBe(text);
});

it("never hides a fence that is not a protocol block", () => {
  const text = '结论。\n\n```harness-ui\n{"not":"a block"}\n```';

  expect(stripHarnessUiFences(text, 2)).toBe(text);
});

it("leaves other code fences alone", () => {
  const text = '```json\n{"kind":"table"}\n```';

  expect(stripHarnessUiFences(text, 2)).toBe(text);
});

it("hides fences only for the blocks the model authored", () => {
  const authored = presentation();
  const projected = presentation({ presentation_id: "ui_2", decision: { origin: "shape" } });

  expect(modelBlockCount([authored, projected])).toBe(1);
  expect(modelBlockCount([projected])).toBe(0);
});

it("stops a chart from crashing when its configuration is unusable", () => {
  render(
    presentation({
      kind: "chart",
      renderer_version: "chart/1",
      data: { rows: [{ quarter: "Q1", count: 120 }] },
      config: { chart_type: "donut", x_field: "quarter", y_fields: ["count"] },
    }),
  );
  // An unrenderable chart is replaced by its sentence rather than an empty box.
  expect(host?.querySelector("svg")).toBeNull();
  expect(host?.textContent).toContain("第一季度 120 件。");
});

it("keeps chart rendering deterministic for the documented config", () => {
  render(
    presentation({
      kind: "chart",
      renderer_version: "chart/1",
      data: { rows: [{ quarter: "Q1", count: 120 }, { quarter: "Q2", count: 150 }] },
      config: { chart_type: "bar", x_field: "quarter", y_fields: ["count"], unit: "件" },
    }),
  );
  expect(host?.querySelectorAll("rect").length).toBeGreaterThan(0);
  expect(host?.textContent).toContain("件");
});

it("never runs a chart timer after unmount", () => {
  vi.useFakeTimers();
  render(
    presentation({
      kind: "chart",
      renderer_version: "chart/1",
      data: { rows: [{ quarter: "Q1", count: 120 }] },
      config: { chart_type: "line", x_field: "quarter", y_fields: ["count"] },
    }),
  );
  act(() => root?.unmount());
  root = undefined;
  expect(() => vi.runOnlyPendingTimers()).not.toThrow();
  vi.useRealTimers();
});
