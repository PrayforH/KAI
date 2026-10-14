"use client";

import { MermaidDiagram } from "../mermaid-diagram";
import { cellText } from "./cells";
import type { ComparisonData, MetricData, SummaryData, TimelineData } from "./schema";

/** A factual snapshot, with the leading fact prominent and provenance adjacent. */
export function SummaryView({ summary }: { summary: SummaryData }) {
  return (
    <div className="intelligent-ui-block intelligent-ui-summary">
      {summary.headline && <p className="intelligent-ui-summary-headline">{summary.headline}</p>}
      <MetricView metrics={summary} />
      {(summary.updated_at || summary.sources.length > 0) && (
        <footer className="intelligent-ui-summary-provenance">
          {summary.updated_at && <span>资料时间：{summary.updated_at}</span>}
          {summary.sources.map((source, index) => (
            <a key={index} href={source.url} target="_blank" rel="noopener noreferrer">{source.label}</a>
          ))}
        </footer>
      )}
    </div>
  );
}

/** The same dimensions, one column per option: the shape a decision has. */
export function ComparisonView({
  comparison,
  highlight,
}: {
  comparison: ComparisonData;
  highlight?: string;
}) {
  const { dimensions, items } = comparison;
  return (
    <div className="intelligent-ui-block intelligent-ui-comparison">
      <div className="intelligent-ui-scroll">
        <table>
          <thead>
            <tr>
              <th scope="col">{highlight || "维度"}</th>
              {items.map((item) => (
                <th key={item.label} scope="col">{item.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {dimensions.map((dimension) => (
              <tr key={dimension.key}>
                <th scope="row">{dimension.label ?? dimension.key}</th>
                {items.map((item) => (
                  <td key={item.label}>{cellText(item.values[dimension.key])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Ordered milestones, earliest first, exactly as the server validated them. */
export function TimelineView({ timeline }: { timeline: TimelineData }) {
  return (
    <div className="intelligent-ui-block intelligent-ui-timeline">
      <ol>
        {timeline.events.map((event, index) => (
          <li key={index}>
            <span className="intelligent-ui-timeline-mark" aria-hidden="true" />
            <div>
              <p className="intelligent-ui-timeline-at">{event.at}</p>
              <p className="intelligent-ui-timeline-title">{event.title}</p>
              {event.detail ? (
                <p className="intelligent-ui-timeline-detail">{event.detail}</p>
              ) : null}
            </div>
          </li>
        ))}
      </ol>
    </div>
  );
}

/** A handful of headline scalars: the numbers a reader would otherwise hunt for. */
export function MetricView({ metrics }: { metrics: MetricData }) {
  return (
    <div className="intelligent-ui-block intelligent-ui-metric">
      {metrics.items.map((item, index) => (
        <div className="intelligent-ui-metric-item" key={index}>
          <span className="intelligent-ui-metric-label">{item.label}</span>
          <span className="intelligent-ui-metric-value">
            {cellText(item.value)}
            {item.unit ? <small>{item.unit}</small> : null}
          </span>
          {item.hint ? <span className="intelligent-ui-metric-hint">{item.hint}</span> : null}
        </div>
      ))}
    </div>
  );
}

/** Diagrams reuse the console's Mermaid renderer — the same component markdown
 * fences use, so styling and failure states stay consistent. */
export function DiagramView({ source }: { source: string }) {
  return (
    <div className="intelligent-ui-block intelligent-ui-diagram">
      <MermaidDiagram code={source} />
    </div>
  );
}
