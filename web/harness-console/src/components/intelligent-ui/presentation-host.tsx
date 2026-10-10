"use client";

import { ChartView } from "./chart-view";
import { type Presentation, parsePresentation, parsePresentationBody } from "./schema";
import { TableView } from "./table-view";
import { ComparisonView, DiagramView, MetricView, TimelineView } from "./views";

/** The one place a decision about "can this bundle show this block" is made.
 * Body parsing happens once, here, so every view below receives validated data
 * and no component has to defend itself against a shape it cannot draw. */
function PresentationView({ presentation }: { presentation: Presentation }) {
  const body = parsePresentationBody(presentation);
  switch (body?.kind) {
    case "table":
      return (
        <TableView
          columns={body.table.columns}
          rows={body.table.rows}
          title={presentation.title}
          caption={body.caption}
        />
      );
    case "chart":
      return <ChartView rows={body.rows} config={body.config} title={presentation.title} />;
    case "comparison":
      return <ComparisonView comparison={body.comparison} highlight={body.highlight} />;
    case "timeline":
      return <TimelineView timeline={body.timeline} />;
    case "metric":
      return <MetricView metrics={body.metrics} />;
    case "diagram":
      return <DiagramView source={body.source} />;
    default:
      return null;
  }
}

function fallbackText(value: unknown): string | null {
  if (!value || typeof value !== "object") return null;
  const text = (value as { fallback_text?: unknown }).fallback_text;
  return typeof text === "string" && text.trim() ? text : null;
}

/** One interactive answer block. A block this bundle cannot render — unknown
 * kind, newer renderer, or a body that does not parse — becomes the sentence the
 * server required alongside it, so an answer never depends on the client. */
export function IntelligentUiBlock({ value }: { value: unknown }) {
  const presentation = parsePresentation(value);
  const body = presentation ? parsePresentationBody(presentation) : null;
  if (!presentation || !body) {
    const text = fallbackText(value);
    return text ? <figure className="intelligent-ui intelligent-ui-unavailable">{text}</figure> : null;
  }
  return (
    <figure className="intelligent-ui" data-kind={presentation.kind} title={presentation.fallback_text}>
      {presentation.title ? (
        <figcaption className="intelligent-ui-title">{presentation.title}</figcaption>
      ) : null}
      <PresentationView presentation={presentation} />
    </figure>
  );
}
