import { z } from "zod";

/** Renderer versions this bundle implements. A block carrying anything else is
 * shown as its `fallback_text`, which is why the server requires one. */
export const SUPPORTED_RENDERER_VERSIONS: Record<string, string> = {
  table: "table/1",
  comparison: "comparison/1",
  chart: "chart/1",
  diagram: "diagram/1",
  timeline: "timeline/1",
  metric: "metric/1",
};

const cellSchema = z.union([z.string(), z.number(), z.boolean(), z.null()]);

const presentationSchema = z.object({
  schema_version: z.literal("harness.ui/v1"),
  presentation_id: z.string().min(1),
  revision: z.number().int().positive(),
  run_id: z.string(),
  message_id: z.string(),
  placement: z.enum(["inline", "workspace"]).default("inline"),
  kind: z.string(),
  renderer_version: z.string(),
  title: z.string().nullish(),
  fallback_text: z.string().min(1),
  data: z.record(z.string(), z.unknown()),
  config: z.record(z.string(), z.unknown()).default({}),
  decision: z.record(z.string(), z.unknown()).default({}),
});

export type Presentation = z.infer<typeof presentationSchema>;
export type PresentationCell = z.infer<typeof cellSchema>;

const columnSchema = z.object({
  key: z.string(),
  label: z.string().nullish(),
  align: z.enum(["left", "right", "center"]).nullish(),
});
export type PresentationColumn = z.infer<typeof columnSchema>;

const tableDataSchema = z.object({
  columns: z.array(columnSchema).min(1),
  rows: z.array(z.record(z.string(), cellSchema)).min(1),
});

const chartRowsSchema = z.object({
  rows: z.array(z.record(z.string(), cellSchema)).min(1),
});

const comparisonDataSchema = z.object({
  dimensions: z.array(columnSchema).min(1),
  items: z
    .array(z.object({ label: z.string(), values: z.record(z.string(), cellSchema) }))
    .min(2),
});

const chartConfigSchema = z.object({
  chart_type: z.enum(["bar", "line", "area", "pie"]),
  x_field: z.string(),
  y_fields: z.array(z.string()).min(1),
  y_labels: z.array(z.string()).optional(),
  x_label: z.string().optional(),
  y_label: z.string().optional(),
  unit: z.string().optional(),
});

const timelineDataSchema = z.object({
  events: z
    .array(z.object({ at: z.string(), title: z.string(), detail: z.string().optional() }))
    .min(1),
});

const metricDataSchema = z.object({
  items: z
    .array(
      z.object({
        label: z.string(),
        value: cellSchema,
        unit: z.string().optional(),
        hint: z.string().optional(),
      }),
    )
    .min(1),
});

export type TableData = z.infer<typeof tableDataSchema>;
export type ComparisonData = z.infer<typeof comparisonDataSchema>;
export type ChartConfig = z.infer<typeof chartConfigSchema>;
export type TimelineData = z.infer<typeof timelineDataSchema>;
export type MetricData = z.infer<typeof metricDataSchema>;
export type ChartRow = Record<string, PresentationCell>;

/** The kind-specific payload, parsed once. A block whose body does not survive
 * parsing is not rendered at all — the caller shows `fallback_text` instead, so
 * a newer server can never leave the reader with an empty box. */
export type PresentationBody =
  | { kind: "table"; table: TableData; caption: string }
  | { kind: "comparison"; comparison: ComparisonData; highlight: string }
  | { kind: "chart"; rows: ChartRow[]; config: ChartConfig }
  | { kind: "diagram"; source: string }
  | { kind: "timeline"; timeline: TimelineData }
  | { kind: "metric"; metrics: MetricData };

function configText(presentation: Presentation, key: string): string {
  const value = presentation.config[key];
  return typeof value === "string" ? value : "";
}

/** Parse one server-authored block. Returns null for anything this bundle must
 * not render — an unknown kind, an unsupported renderer version, or a shape the
 * component would trip over. Callers then show `fallback_text`. */
export function parsePresentation(value: unknown): Presentation | null {
  const parsed = presentationSchema.safeParse(value);
  if (!parsed.success) return null;
  if (SUPPORTED_RENDERER_VERSIONS[parsed.data.kind] !== parsed.data.renderer_version) return null;
  return parsed.data;
}

export function parsePresentationBody(presentation: Presentation): PresentationBody | null {
  switch (presentation.kind) {
    case "table": {
      const table = tableDataSchema.safeParse(presentation.data);
      return table.success
        ? { kind: "table", table: table.data, caption: configText(presentation, "caption") }
        : null;
    }
    case "comparison": {
      const comparison = comparisonDataSchema.safeParse(presentation.data);
      return comparison.success
        ? { kind: "comparison", comparison: comparison.data, highlight: configText(presentation, "highlight") }
        : null;
    }
    case "chart": {
      const rows = chartRowsSchema.safeParse(presentation.data);
      const config = chartConfigSchema.safeParse(presentation.config);
      return rows.success && config.success
        ? { kind: "chart", rows: rows.data.rows, config: config.data }
        : null;
    }
    case "diagram": {
      const source = presentation.data.mermaid;
      return typeof source === "string" && source.trim() ? { kind: "diagram", source } : null;
    }
    case "timeline": {
      const timeline = timelineDataSchema.safeParse(presentation.data);
      return timeline.success ? { kind: "timeline", timeline: timeline.data } : null;
    }
    case "metric": {
      const metrics = metricDataSchema.safeParse(presentation.data);
      return metrics.success ? { kind: "metric", metrics: metrics.data } : null;
    }
    default:
      return null;
  }
}

/** Tool-part shape the console receives a presentation on, live and restored. */
type ContentPartLike = { type?: string; toolName?: string; args?: unknown; result?: unknown };

/** The interactive blocks this turn shows, in the order the server published them. */
export function presentationsForMessage(parts: readonly ContentPartLike[]): Presentation[] {
  const seen = new Set<string>();
  const presentations: Presentation[] = [];
  for (const part of parts) {
    if (part.type !== "tool-call" || part.toolName !== "harness_present_ui") continue;
    const presentation = parsePresentation(part.args);
    if (!presentation || seen.has(presentation.presentation_id)) continue;
    seen.add(presentation.presentation_id);
    presentations.push(presentation);
  }
  return presentations;
}

const UI_FENCE = /```([^\n]*)\n([\s\S]*?)\n```/g;

function isProtocolBlock(body: string): boolean {
  try {
    const parsed = JSON.parse(body);
    return Boolean(parsed) && typeof parsed === "object" && typeof parsed.kind === "string";
  } catch {
    return false;
  }
}

/** How many of this turn's blocks came from a fence the model wrote, in order.
 * Only those may be hidden from the rendered answer: the projected blocks never
 * had a fence, and a rejected fence must stay visible. */
export function modelBlockCount(presentations: readonly Presentation[]): number {
  return presentations.filter((item) => item.decision.origin === "model").length;
}

export function stripHarnessUiFences(text: string, published: number): string {
  if (published <= 0) return text;
  let remaining = published;
  return text.replace(UI_FENCE, (match, language: string, body: string) => {
    if (remaining <= 0 || language.trim().toLowerCase() !== "harness-ui") return match;
    if (!isProtocolBlock(body)) return match;
    remaining -= 1;
    return "";
  });
}
