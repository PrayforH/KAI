// @vitest-environment jsdom
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { IntelligentUiBlock } from "../src/components/intelligent-ui/presentation-host";
import {
  actionMessage,
  formAction,
  modelBlockSources,
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
  expect(
    parsePresentation(block({ schema_version: "harness.ui/v2" })),
  ).toBeNull();
});

it("collects the turn's blocks from tool-call parts, newest fields intact", () => {
  const parts = [
    { type: "text" },
    { type: "tool-call", toolName: "harness_run_activity", args: {} },
    { type: "tool-call", toolName: "harness_present_ui", args: presentation() },
    { type: "tool-call", toolName: "harness_present_ui", args: presentation() },
    {
      type: "tool-call",
      toolName: "harness_present_ui",
      args: presentation({ presentation_id: "ui_2" }),
    },
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

it("keeps comparison values aligned with their option and dimension", () => {
  render(
    presentation({
      kind: "comparison",
      renderer_version: "comparison/1",
      data: {
        dimensions: [
          { key: "cost", label: "成本" },
          { key: "replicas", label: "副本数" },
        ],
        items: [
          { label: "方案甲", values: { cost: 0, replicas: 1 } },
          { label: "方案乙", values: { cost: null, replicas: 2 } },
        ],
      },
    }),
  );
  const rows = [...host!.querySelectorAll("tr")].map((row) =>
    [...row.children].map((cell) => cell.textContent),
  );
  expect(rows).toEqual([
    ["维度", "方案甲", "方案乙"],
    ["成本", "0", "—"],
    ["副本数", "1", "2"],
  ]);
});

it("renders each timeline milestone with its own date and explanation", () => {
  render(
    presentation({
      kind: "timeline",
      renderer_version: "timeline/1",
      data: {
        events: [
          { at: "2026-10-01", title: "需求确认", detail: "确定范围" },
          { at: "2026-10-05", title: "协议冻结", detail: "固定协议" },
        ],
      },
    }),
  );
  const milestones = [...host!.querySelectorAll("ol > li")].map(
    (item) => item.textContent,
  );
  expect(milestones).toEqual([
    "2026-10-01需求确认确定范围",
    "2026-10-05协议冻结固定协议",
  ]);
});

it("hides only the fences the server actually published", () => {
  const first = '```harness-ui\n{"version":1,"kind":"table"}\n```';
  const second = '```harness-ui\n{"version":1,"kind":"metric"}\n```';
  const text = `结论。\n\n${first}\n\n${second}`;

  expect(stripHarnessUiFences(text, [first, second])).toBe("结论。\n\n\n\n");
  // A rejected block stays visible: a hole where the model's content used to be
  // is worse than a code block.
  expect(stripHarnessUiFences(text, [first])).toBe(`结论。\n\n\n\n${second}`);
  expect(stripHarnessUiFences(text, [])).toBe(text);
});

it("never hides a fence that is not a protocol block", () => {
  const text = '结论。\n\n```harness-ui\n{"not":"a block"}\n```';

  expect(stripHarnessUiFences(text, [])).toBe(text);
});

it("leaves other code fences alone", () => {
  const text = '```json\n{"kind":"table"}\n```';

  expect(stripHarnessUiFences(text, [])).toBe(text);
});

it("hides fences only for the blocks the model authored", () => {
  const authored = presentation({ source_text: "accepted source" });
  const projected = presentation({
    presentation_id: "ui_2",
    decision: { origin: "shape" },
  });

  expect(modelBlockSources([authored, projected])).toEqual(["accepted source"]);
  expect(modelBlockSources([projected])).toEqual([]);
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
      data: {
        rows: [
          { quarter: "Q1", count: 120 },
          { quarter: "Q2", count: 150 },
        ],
      },
      config: {
        chart_type: "bar",
        x_field: "quarter",
        y_fields: ["count"],
        unit: "件",
      },
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

it("renders a form block with its fields and submit label", () => {
  const value = presentation({
    kind: "form",
    renderer_version: "form/1",
    data: {
      fields: [
        {
          key: "project",
          label: "项目名称",
          type: "text",
          required: true,
          options: [],
          placeholder: "",
        },
        {
          key: "level",
          label: "优先级",
          type: "single",
          required: false,
          options: ["高", "低"],
          placeholder: "",
        },
      ],
    },
    config: { submit_label: "提交立项" },
    actions: [
      {
        action_id: "a0",
        kind: "write",
        label: "提交立项",
        prompt: "请提交",
        style: "primary",
      },
    ],
  });
  root = createRoot(host as HTMLDivElement);
  act(() =>
    root?.render(<IntelligentUiBlock value={value} onAction={() => {}} />),
  );

  expect(host?.textContent).toContain("项目名称");
  expect(host?.textContent).toContain("优先级");
  expect(host?.textContent).toContain("提交立项");
  expect(host?.querySelectorAll("input").length).toBeGreaterThan(0);
});

it("sends the action's instruction together with what the user filled in", () => {
  const actions = [
    {
      action_id: "a0",
      kind: "write" as const,
      label: "提交",
      prompt: "请按以下信息发起提交",
      style: "primary" as const,
    },
  ];
  const parsed = presentation({
    kind: "form",
    renderer_version: "form/1",
    data: {
      fields: [
        {
          key: "project",
          label: "项目名称",
          type: "text",
          required: true,
          options: [],
          placeholder: "",
        },
      ],
    },
    config: { submit_label: "提交" },
    actions,
  });

  expect(actionMessage(parsed.actions[0], "项目名称：星火")).toBe(
    "请按以下信息发起提交\n\n项目名称：星火",
  );
  // An action with nothing filled in still sends exactly what it declares.
  expect(actionMessage(parsed.actions[0], "   ")).toBe("请按以下信息发起提交");
});

it("picks a form's submit action from the ones the server published", () => {
  const onlyConversation = presentation({
    actions: [
      {
        action_id: "a0",
        kind: "conversation",
        label: "聊聊",
        prompt: "请聊聊",
        style: "default",
      },
    ],
  });
  const withSubmit = presentation({
    actions: [
      {
        action_id: "a0",
        kind: "conversation",
        label: "聊聊",
        prompt: "请聊聊",
        style: "default",
      },
      {
        action_id: "a1",
        kind: "write",
        label: "提交",
        prompt: "请提交",
        style: "primary",
      },
    ],
  });

  expect(formAction(onlyConversation)).toBeNull();
  expect(formAction(withSubmit)?.action_id).toBe("a1");
});

it("offers the block's actions as buttons and reports the click", async () => {
  const calls: string[] = [];
  const value = presentation({
    actions: [
      {
        action_id: "a0",
        kind: "conversation",
        label: "解释这一行",
        prompt: "请展开说明",
        style: "default",
      },
    ],
  });
  root = createRoot(host as HTMLDivElement);
  act(() =>
    root?.render(
      <IntelligentUiBlock
        value={value}
        onAction={(_p, action) => {
          calls.push(action.action_id);
        }}
      />,
    ),
  );
  const button = [...(host?.querySelectorAll("button") ?? [])].find((item) =>
    item.textContent?.includes("解释这一行"),
  );
  expect(button).toBeTruthy();
  await act(async () => {
    button?.click();
  });
  expect(calls).toEqual(["a0"]);
});

it("keeps a table's actions hidden when the turn cannot send", () => {
  render(
    presentation({
      actions: [
        {
          action_id: "a0",
          kind: "conversation",
          label: "解释",
          prompt: "请解释",
          style: "default",
        },
      ],
    }),
  );
  expect(host?.querySelector(".intelligent-ui-actions")).toBeNull();
});

it("keeps rejected candidates before the exact accepted source", () => {
  const rejected = '```harness-ui\n{"version":1,"kind":"unsupported"}\n```';
  const accepted =
    '```harness-ui\n{"version":1,"kind":"table","data":{"rows":[{"x":1}]}}\n```';
  expect(stripHarnessUiFences(rejected + "\n" + accepted, [accepted])).toBe(
    rejected + "\n",
  );
  expect(stripHarnessUiFences(accepted + "\n" + accepted, [accepted])).toBe(
    "\n" + accepted,
  );
});

it("removes only an accepted bare object when the model omitted the fence", () => {
  const source = '{"version":1,"kind":"metric","data":{}}';
  expect(stripHarnessUiFences("结论。\n" + source, [source])).toBe("结论。\n");
});

it("reports a clipboard failure instead of claiming success", async () => {
  Object.defineProperty(navigator, "clipboard", {
    value: undefined,
    configurable: true,
  });
  render(presentation());
  const button = [...host!.querySelectorAll("button")].find(
    (b) => b.textContent === "复制",
  )!;
  await act(async () => {
    button.click();
  });
  expect(host!.textContent).not.toContain("已复制");
  expect(host!.querySelector('[role="alert"]')?.textContent).toContain(
    "复制失败",
  );
});

it("copies successfully through the HTTP selection fallback", async () => {
  Object.defineProperty(navigator, "clipboard", {
    value: undefined,
    configurable: true,
  });
  Object.defineProperty(document, "execCommand", {
    value: vi.fn(() => true),
    configurable: true,
  });
  render(presentation());
  const button = [...host!.querySelectorAll("button")].find(
    (b) => b.textContent === "复制",
  )!;
  await act(async () => {
    button.click();
  });
  expect(host!.textContent).toContain("已复制");
  expect(document.execCommand).toHaveBeenCalledWith("copy");
  expect(document.querySelector("textarea")).toBeNull();
});

it("draws both negative and positive bars within the chart", () => {
  render(
    presentation({
      kind: "chart",
      renderer_version: "chart/1",
      data: {
        rows: [
          { month: "一月", profit: -20 },
          { month: "二月", profit: 30 },
        ],
      },
      config: { chart_type: "bar", x_field: "month", y_fields: ["profit"] },
    }),
  );
  const bars = host!.querySelectorAll("svg rect");
  expect(bars).toHaveLength(2);
  for (const bar of bars) {
    expect(Number(bar.getAttribute("height"))).toBeGreaterThan(0);
    expect(Number(bar.getAttribute("y"))).toBeGreaterThanOrEqual(16);
    expect(
      Number(bar.getAttribute("y")) + Number(bar.getAttribute("height")),
    ).toBeLessThanOrEqual(216);
  }
});

it("does not connect a line across a missing sample", () => {
  render(
    presentation({
      kind: "chart",
      renderer_version: "chart/1",
      data: {
        rows: [
          { x: "a", y: 10 },
          { x: "b", y: null },
          { x: "c", y: 20 },
        ],
      },
      config: { chart_type: "line", x_field: "x", y_fields: ["y"] },
    }),
  );
  expect(host!.querySelectorAll("polyline")).toHaveLength(2);
  expect(host!.querySelectorAll("circle")).toHaveLength(2);
});

it("draws a full circle for a single nonzero pie category", () => {
  render(
    presentation({
      kind: "chart",
      renderer_version: "chart/1",
      data: { rows: [{ x: "a", y: 10 }] },
      config: { chart_type: "pie", x_field: "x", y_fields: ["y"] },
    }),
  );
  expect(host!.querySelector("svg circle")).not.toBeNull();
});

it("keeps field keys and reuses the submission key after a lost response", async () => {
  const onAction = vi
    .fn()
    .mockRejectedValueOnce(new Error("连接中断"))
    .mockResolvedValueOnce(undefined);
  const value = presentation({
    kind: "form",
    renderer_version: "form/1",
    data: {
      fields: [
        {
          key: "project",
          label: "项目",
          type: "text",
          required: true,
          options: [],
        },
      ],
    },
    actions: [
      {
        action_id: "a1",
        kind: "write",
        label: "提交",
        prompt: "提交项目",
        style: "primary",
      },
    ],
  });
  root = createRoot(host!);
  act(() =>
    root!.render(<IntelligentUiBlock value={value} onAction={onAction} />),
  );
  const input = host!.querySelector("input")!;
  act(() => {
    Object.getOwnPropertyDescriptor(
      HTMLInputElement.prototype,
      "value",
    )!.set!.call(input, "测试项目");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  const button = () =>
    [...host!.querySelectorAll("button")].find(
      (b) => b.textContent === "提交",
    )!;
  await act(async () => button().click());
  expect(host!.textContent).toContain("连接中断");
  expect(input.value).toBe("测试项目");
  await act(async () => button().click());
  expect(onAction.mock.calls[0][2]).toEqual({ project: ["测试项目"] });
  expect(onAction.mock.calls[0][3]).toBeTruthy();
  expect(onAction.mock.calls[1][3]).toBe(onAction.mock.calls[0][3]);
});

it("restores a submitted write and its inputs without offering a second submit", async () => {
  const value = presentation({
    kind: "form",
    renderer_version: "form/1",
    data: {
      fields: [
        {
          key: "project",
          label: "项目",
          type: "text",
          required: true,
          options: [],
        },
      ],
    },
    actions: [
      {
        action_id: "a1",
        kind: "write",
        label: "提交",
        prompt: "提交项目",
        style: "primary",
      },
    ],
  });
  const fetcher = vi.spyOn(globalThis, "fetch").mockResolvedValue(
    Response.json({
      ...value,
      view_state: {
        version: 3,
        state: { values: { project: ["已提交项目"] } },
      },
      action_requests: [
        {
          request_id: "q1",
          action_id: "a1",
          revision: 1,
          run_id: "run-child",
          execution_status: "succeeded",
        },
      ],
    }),
  );
  try {
    root = createRoot(host!);
    await act(async () =>
      root!.render(
        <IntelligentUiBlock value={value} onAction={vi.fn()} connected />,
      ),
    );
    expect(host!.querySelector("input")!.value).toBe("已提交项目");
    expect(host!.querySelector("input")!.disabled).toBe(true);
    expect(host!.textContent).toContain("执行完成");
    expect(fetcher).toHaveBeenCalledWith(
      "/api/ui/presentations/ui_1?revision=1",
      expect.anything(),
    );
  } finally {
    fetcher.mockRestore();
  }
});

it("persists restored table sorting with its state version", async () => {
  vi.useFakeTimers();
  const value = presentation();
  const fetcher = vi
    .spyOn(globalThis, "fetch")
    .mockResolvedValueOnce(
      Response.json({
        ...value,
        view_state: {
          version: 4,
          state: { sort: { key: "count", descending: true } },
        },
        action_requests: [],
      }),
    )
    .mockResolvedValueOnce(Response.json({ version: 5 }));
  try {
    root = createRoot(host!);
    await act(async () =>
      root!.render(<IntelligentUiBlock value={value} connected />),
    );
    expect(host!.querySelectorAll("tbody tr")[0].textContent).toContain(
      "第二季度",
    );
    act(() =>
      host!
        .querySelectorAll("th button")[1]
        .dispatchEvent(new MouseEvent("click", { bubbles: true })),
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(400);
    });
    expect(JSON.parse(fetcher.mock.calls[1][1]!.body as string)).toEqual({
      revision: 1,
      expected_version: 4,
      state: { sort: { key: "count", descending: false } },
    });
  } finally {
    fetcher.mockRestore();
    vi.useRealTimers();
  }
});
