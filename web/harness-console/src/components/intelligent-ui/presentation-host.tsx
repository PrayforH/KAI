"use client";

import { useRef, useState } from "react";
import {
  executionLabel,
  terminalExecution,
  type UiExecution,
  type UiViewState,
} from "./client";
import { usePresentation } from "./use-presentation";

import { ChartView } from "./chart-view";
import { FormView, type FieldValues } from "./form-view";
import { HtmlView } from "./html-view";
import { PresentationMenu } from "./presentation-menu";
import {
  type Presentation,
  type PresentationAction,
  formAction,
  parsePresentation,
  parsePresentationBody,
} from "./schema";
import { TableView } from "./table-view";
import { ComparisonView, DiagramView, MetricView, SummaryView, TimelineView } from "./views";

/** What a block hands back to the turn when the user acts on it. */
export type PresentationActionHandler = (
  presentation: Presentation,
  action: PresentationAction,
  values: FieldValues,
  idempotencyKey: string,
) => void | UiExecution | Promise<void | UiExecution>;

function PresentationBodyView({
  body,
  presentation,
  onAction,
  disabled,
  loaded,
  view,
  onView,
}: {
  view: UiViewState;
  onView: (state: UiViewState) => void;
  body: NonNullable<ReturnType<typeof parsePresentationBody>>;
  presentation: Presentation;
  onAction?: PresentationActionHandler;
  disabled: boolean;
  loaded: boolean;
}) {
  switch (body.kind) {
    case "summary":
      return <SummaryView summary={body.summary} />;
    case "html":
      return <HtmlView key={`${presentation.presentation_id}:${presentation.revision}`}
        presentation={presentation} height={body.height} initialState={view.html_state}
        ready={loaded} onState={(html_state) => onView({ html_state })} />;
    case "table":
      return (
        <TableView
          columns={body.table.columns}
          rows={body.table.rows}
          title={presentation.title}
          caption={body.caption}
          sort={view.sort}
          onSort={(sort) => onView({ sort })}
        />
      );
    case "chart":
      return (
        <ChartView
          rows={body.rows}
          config={body.config}
          title={presentation.title}
          showData={view.show_data}
          onShowData={(show_data) => onView({ show_data })}
        />
      );
    case "comparison":
      return (
        <ComparisonView
          comparison={body.comparison}
          highlight={body.highlight}
        />
      );
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
          singleUse={action?.kind === "write"}
          values={view.values}
          onChange={(values) => onView({ values })}
          onSubmit={
            action && onAction
              ? async (values) => {
                  await onAction(presentation, action, values, "");
                }
              : undefined
          }
        />
      );
    }
    default:
      return null;
  }
}

/** Buttons request a server-owned action; accepting a request is not success. */
function ActionBar({
  presentation,
  onAction,
  disabled,
  requests,
}: {
  requests: UiExecution[];
  presentation: Presentation;
  onAction?: PresentationActionHandler;
  disabled: boolean;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState("");
  if (!onAction) return null;
  // A form owns its own submit button; showing both would offer the same turn twice.
  const actions = presentation.actions.filter(
    (action) =>
      !(
        presentation.kind === "form" &&
        (action.kind === "read" || action.kind === "write")
      ),
  );
  if (actions.length === 0) return null;

  async function run(action: PresentationAction) {
    if (busy) return;
    setBusy(action.action_id);
    setError("");
    try {
      await onAction?.(presentation, action, {}, "");
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
            disabled={
              disabled ||
              busy !== null ||
              (action.kind === "write" &&
                requests.some((r) => r.action_id === action.action_id))
            }
            onClick={() => void run(action)}
          >
            {busy === action.action_id ? "发送中…" : action.label}
          </button>
        ))}
      </div>
      {error ? (
        <p role="alert" className="intelligent-ui-form-error">
          {error}
        </p>
      ) : null}
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
  connected = false,
  onSettled,
}: {
  connected?: boolean;
  onSettled?: (runId: string) => void;
  value: unknown;
  onAction?: PresentationActionHandler;
  disabled?: boolean;
}) {
  const presentation = parsePresentation(value);
  const body = presentation ? parsePresentationBody(presentation) : null;
  if (presentation?.decision.display === "text") {
    return <p>{presentation.fallback_text}</p>;
  }
  if (!presentation || !body) {
    const text = fallbackText(value);
    return text ? (
      <figure className="intelligent-ui intelligent-ui-unavailable">
        {text}
      </figure>
    ) : null;
  }
  return (
    <PublishedPresentation
      presentation={presentation}
      body={body}
      onAction={onAction}
      disabled={disabled}
      connected={connected}
      onSettled={onSettled}
    />
  );
}

function PublishedPresentation({
  presentation,
  body,
  onAction,
  disabled,
  connected,
  onSettled,
}: {
  presentation: Presentation;
  body: NonNullable<ReturnType<typeof parsePresentationBody>>;
  onAction?: PresentationActionHandler;
  disabled: boolean;
  connected: boolean;
  onSettled?: (runId: string) => void;
}) {
  const state = usePresentation(presentation, connected, onSettled);
  const keys = useRef(new Map<string, { payload: string; key: string }>());
  const handle: PresentationActionHandler | undefined = onAction
    ? async (p, action, values) => {
        const payload = JSON.stringify([p.revision, values]);
        let saved = keys.current.get(action.action_id);
        if (!saved || saved.payload !== payload) {
          saved = {
            payload,
            key:
              globalThis.crypto?.randomUUID?.() ??
              `ui-${Date.now()}-${Math.random().toString(36).slice(2)}`,
          };
          keys.current.set(action.action_id, saved);
        }
        const request = await onAction(p, action, values, saved.key);
        state.accept(request);
        return request;
      }
    : undefined;
  const active = state.requests.some(
    (r) => !terminalExecution(r.execution_status),
  );
  const form = formAction(presentation);
  const formSubmitted =
    form?.kind === "write" &&
    state.requests.some((r) => r.action_id === form.action_id);
  return (
    <figure className="intelligent-ui" data-kind={presentation.kind}>
      {body.kind === "html" ? <figcaption className="intelligent-ui-title intelligent-ui-html-title">
        <span>{presentation.title || "交互工具"}</span>
        <PresentationMenu presentation={presentation} />
      </figcaption> : presentation.title ? (
        <figcaption className="intelligent-ui-title">
          {presentation.title}
        </figcaption>
      ) : null}
      {!state.loaded && state.error ? <button type="button" onClick={state.retry}>重新加载交互工具</button> : <PresentationBodyView
        body={body}
        presentation={presentation}
        onAction={handle}
        disabled={disabled || !state.loaded || active || Boolean(formSubmitted)}
        loaded={state.loaded}
        view={state.view}
        onView={state.changeView}
      />}
      <ActionBar
        presentation={presentation}
        onAction={handle}
        disabled={disabled || !state.loaded || active}
        requests={state.requests}
      />
      {state.error ? (
        <p role="alert" className="intelligent-ui-form-error">
          {state.error}
        </p>
      ) : null}
      {state.requests.map((request) => (
        <div
          className="intelligent-ui-note"
          key={request.request_id}
          role="status"
        >
          {
            presentation.actions.find((a) => a.action_id === request.action_id)
              ?.label
          }{" "}
          · {executionLabel(request.execution_status)}
          {request.error_code ? (
            <span> · 请求未完成，请核对任务详情</span>
          ) : null}
        </div>
      ))}
      {body.kind !== "html" && <details className="intelligent-ui-note">
        <summary>数据与来源</summary>
        <p>{presentation.fallback_text}</p>
        <p>
          {presentation.decision.origin === "shape"
            ? "基于本轮工具返回的数据"
            : "基于本轮回答中的数据"}{" "}
          · 版本 {presentation.revision}
        </p>
        {presentation.snapshot_hash ? (
          <p title={presentation.snapshot_hash}>
            数据快照 {presentation.snapshot_hash.slice(0, 12)}
          </p>
        ) : null}
      </details>}
    </figure>
  );
}

function fallbackText(value: unknown): string | null {
  if (!value || typeof value !== "object") return null;
  const text = (value as { fallback_text?: unknown }).fallback_text;
  return typeof text === "string" && text.trim() ? text : null;
}
