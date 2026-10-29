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
  summary: "summary/1",
  form: "form/1",
  html: "html/1",
};

const cellSchema = z.union([z.string(), z.number(), z.boolean(), z.null()]);

const actionSchema = z.object({
  action_id: z.string().min(1),
  kind: z.enum(["conversation", "read", "write"]),
  label: z.string().min(1),
  prompt: z.string().default(""),
  style: z.enum(["primary", "default"]).default("default"),
});

export type PresentationAction = z.infer<typeof actionSchema>;

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
  actions: z.array(actionSchema).max(4).default([]),
  decision: z.record(z.string(), z.unknown()).default({}),
  source_text: z.string().nullish(),
  source_refs: z.array(z.record(z.string(), z.unknown())).default([]),
  snapshot_hash: z.string().default(""),
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
    .array(
      z.object({ label: z.string(), values: z.record(z.string(), cellSchema) }),
    )
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
    .array(
      z.object({
        at: z.string(),
        title: z.string(),
        detail: z.string().optional(),
      }),
    )
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

const summaryDataSchema = metricDataSchema.extend({
  items: metricDataSchema.shape.items.max(8),
  headline: z.string().max(600).default(""),
  subtitle: z.string().max(600).default(""),
  updated_at: z.string().max(120).default(""),
  sources: z.array(z.object({
    label: z.string().min(1).max(120),
    url: z.url().refine((value) => {
      const url = new URL(value);
      return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password;
    }),
  }).strict()).max(4).default([]),
}).strict();

const formFieldSchema = z.object({
  key: z.string().min(1),
  label: z.string(),
  type: z.enum(["text", "number", "date", "single", "multi"]),
  required: z.boolean().default(false),
  options: z.array(z.string()).default([]),
  placeholder: z.string().default(""),
});

const formDataSchema = z.object({ fields: z.array(formFieldSchema).min(1) });

const htmlDataSchema = z.object({
  artifact_id: z.string().regex(/^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/),
  name: z.string().max(600),
  sha256: z.string().regex(/^[0-9a-f]{64}$/),
  size_bytes: z.number().int().positive().max(256 * 1024),
}).strict();
const htmlConfigSchema = z.object({ height: z.number().int().min(320).max(900) }).strict();

export type FormField = z.infer<typeof formFieldSchema>;
export type FormData = z.infer<typeof formDataSchema>;

export type TableData = z.infer<typeof tableDataSchema>;
export type ComparisonData = z.infer<typeof comparisonDataSchema>;
export type ChartConfig = z.infer<typeof chartConfigSchema>;
export type TimelineData = z.infer<typeof timelineDataSchema>;
export type MetricData = z.infer<typeof metricDataSchema>;
export type SummaryData = z.infer<typeof summaryDataSchema>;
export type ChartRow = Record<string, PresentationCell>;

/** The kind-specific payload, parsed once. A block whose body does not survive
 * parsing is not rendered at all — the caller shows `fallback_text` instead, so
 * a newer server can never leave the reader with an empty box. */
export type PresentationBody =
  | { kind: "html"; height: number }
  | { kind: "table"; table: TableData; caption: string }
  | { kind: "comparison"; comparison: ComparisonData; highlight: string }
  | { kind: "chart"; rows: ChartRow[]; config: ChartConfig }
  | { kind: "diagram"; source: string }
  | { kind: "timeline"; timeline: TimelineData }
  | { kind: "metric"; metrics: MetricData }
  | { kind: "summary"; summary: SummaryData }
  | { kind: "form"; form: FormData; submitLabel: string };

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
  if (
    SUPPORTED_RENDERER_VERSIONS[parsed.data.kind] !==
    parsed.data.renderer_version
  )
    return null;
  return parsed.data;
}

export function parsePresentationBody(
  presentation: Presentation,
): PresentationBody | null {
  switch (presentation.kind) {
    case "summary": {
      const summary = summaryDataSchema.safeParse(presentation.data);
      return summary.success ? { kind: "summary", summary: summary.data } : null;
    }
    case "html": {
      const data = htmlDataSchema.safeParse(presentation.data);
      const config = htmlConfigSchema.safeParse(presentation.config);
      return data.success && config.success ? { kind: "html", height: config.data.height } : null;
    }
    case "table": {
      const table = tableDataSchema.safeParse(presentation.data);
      return table.success
        ? {
            kind: "table",
            table: table.data,
            caption: configText(presentation, "caption"),
          }
        : null;
    }
    case "comparison": {
      const comparison = comparisonDataSchema.safeParse(presentation.data);
      return comparison.success
        ? {
            kind: "comparison",
            comparison: comparison.data,
            highlight: configText(presentation, "highlight"),
          }
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
      return typeof source === "string" && source.trim()
        ? { kind: "diagram", source }
        : null;
    }
    case "timeline": {
      const timeline = timelineDataSchema.safeParse(presentation.data);
      return timeline.success
        ? { kind: "timeline", timeline: timeline.data }
        : null;
    }
    case "metric": {
      const metrics = metricDataSchema.safeParse(presentation.data);
      return metrics.success ? { kind: "metric", metrics: metrics.data } : null;
    }
    case "form": {
      const form = formDataSchema.safeParse(presentation.data);
      if (!form.success) return null;
      const submit = configText(presentation, "submit_label");
      return { kind: "form", form: form.data, submitLabel: submit || "提交" };
    }
    default:
      return null;
  }
}

/** The action a form submits through: its declared `read`/`write`, primary first.
 * The server refuses a form without one, so this is a pick, not a fallback. */
export function formAction(
  presentation: Presentation,
): PresentationAction | null {
  const candidates = presentation.actions.filter(
    (action) => action.kind === "read" || action.kind === "write",
  );
  return (
    candidates.find((action) => action.style === "primary") ??
    candidates[0] ??
    null
  );
}

/** What clicking an action sends. The action contributes the instruction and the
 * user contributes the selection; both are plain message text. */
export function actionMessage(
  action: PresentationAction,
  value: string,
): string {
  const trimmed = value.trim();
  if (!trimmed) return action.prompt;
  return `${action.prompt}\n\n${trimmed}`;
}

/** Tool-part shape the console receives a presentation on, live and restored. */
type ContentPartLike = {
  type?: string;
  toolName?: string;
  args?: unknown;
  result?: unknown;
};

/** The interactive blocks this turn shows, in the order the server published them. */
export function presentationsForMessage(
  parts: readonly ContentPartLike[],
): Presentation[] {
  const seen = new Set<string>();
  const presentations: Presentation[] = [];
  for (const part of parts) {
    if (part.type !== "tool-call" || part.toolName !== "harness_present_ui")
      continue;
    const presentation = parsePresentation(part.args);
    if (!presentation || seen.has(presentation.presentation_id)) continue;
    seen.add(presentation.presentation_id);
    presentations.push(presentation);
  }
  return presentations;
}

/** Only remove the exact source candidates the server accepted, once each.
 * Matching by count can hide a rejected candidate before a valid one. */
export function modelBlockSources(
  presentations: readonly Presentation[],
): string[] {
  return presentations.flatMap((item) =>
    item.decision.origin === "model" && item.source_text
      ? [item.source_text]
      : [],
  );
}

export function stripHarnessUiFences(
  text: string,
  sources: readonly string[],
): string {
  let result = text;
  for (const source of sources) {
    if (!source) continue;
    const index = result.indexOf(source);
    if (index >= 0)
      result = result.slice(0, index) + result.slice(index + source.length);
  }
  return result;
}
