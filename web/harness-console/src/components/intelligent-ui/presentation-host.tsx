"use client";

import { useState } from "react";

import { ChartView } from "./chart-view";
import { FormView } from "./form-view";
import {
  type Presentation,
  type PresentationAction,
  actionMessage,
  formAction,
  parsePresentation,
  parsePresentationBody,
} from "./schema";
import { TableView } from "./table-view";
import { ComparisonView, DiagramView, MetricView, TimelineView } from "./views";

/** What a block hands back to the turn when the user acts on it. */
export type PresentationActionHandler = (
  presentation: Presentation,
  action: PresentationAction,
  value: string,
) => void | Promise<void>;

function PresentationBodyView({
  body,
  presentation,
  onAction,
  disabled,
}: {
  body: NonNullable<ReturnType<typeof parsePresentationBody>>;
  presentation: Presentation;
  onAction?: PresentationActionHandler;
  disabled: boolean;
}) {
  switch (body.kind) {
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
    case "form": {
      const action = formAction(presentation);
      return (
        <FormView
          fields={body.form.fields}
          submitLabel={body.submitLabel || action?.label || "提交"}
          disabled={disabled}
          onSubmit={action && onAction ? (value) => onAction(presentation, action, value) : undefined}
        />
      );
    }
    default:
      return null;
  }
}

/** The buttons a block offers. They are the only place a block can reach beyond
 * itself, and all they do is put text in the composer's place: the platform sees
 * an ordinary user turn, never a privileged call. */
function ActionBar({
  presentation,
  onAction,
  disabled,
}: {
  presentation: Presentation;
  onAction?: PresentationActionHandler;
  disabled: boolean;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  if (!onAction) return null;
  // A form owns its own submit button; showing both would offer the same turn twice.
  const actions = presentation.actions.filter(
    (action) => !(presentation.kind === "form" && (action.kind === "read" || action.kind === "write")),
  );
  if (actions.length === 0) return null;

  async function run(action: PresentationAction) {
    if (busy) return;
    setBusy(action.action_id);
    setError("");
    try {
      await onAction?.(presentation, action, "");
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : "发送失败，请重试");
    } finally {
      setBusy(null);
    }
  }

  return (
    <>
      <div className="intelligent-ui-actions">
        {actions.map((action) => (
          <button
            key={action.action_id}
            type="button"
            data-style={action.style}
            disabled={disabled || busy !== null}
            onClick={() => void run(action)}
          >
            {busy === action.action_id ? "发送中…" : action.label}
          </button>
        ))}
      </div>
      {error ? <p role="alert" className="intelligent-ui-form-error">{error}</p> : null}
    </>
  );
}

/** One interactive answer block. A block this bundle cannot render — unknown
 * kind, newer renderer, or a body that does not parse — becomes the sentence the
 * server required alongside it, so an answer never depends on the client. */
export function IntelligentUiBlock({
  value,
  onAction,
  disabled = false,
}: {
  value: unknown;
  onAction?: PresentationActionHandler;
  disabled?: boolean;
}) {
  const presentation = parsePresentation(value);
  const body = presentation ? parsePresentationBody(presentation) : null;
  if (!presentation || !body) {
    const text = fallbackText(value);
    return text ? <figure className="intelligent-ui intelligent-ui-unavailable">{text}</figure> : null;
  }
  return (
    <figure
      className="intelligent-ui"
      data-kind={presentation.kind}
      title={presentation.fallback_text}
    >
      {presentation.title ? (
        <figcaption className="intelligent-ui-title">{presentation.title}</figcaption>
      ) : null}
      <PresentationBodyView
        body={body}
        presentation={presentation}
        onAction={onAction}
        disabled={disabled}
      />
      <ActionBar presentation={presentation} onAction={onAction} disabled={disabled} />
    </figure>
  );
}

function fallbackText(value: unknown): string | null {
  if (!value || typeof value !== "object") return null;
  const text = (value as { fallback_text?: unknown }).fallback_text;
  return typeof text === "string" && text.trim() ? text : null;
}
