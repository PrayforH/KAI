# KAI Web Session Protocol v1

KAI WebUI does not bind components to AG-UI or to provider-specific OpenAI Responses events.

The browser owns a small domain protocol:

- `run.started`, `run.finished`, `run.error`
- `message.started`, `message.delta`, `message.finished`
- `tool.started`, `tool.arguments`, `tool.finished`, `tool.result`
- `activity.snapshot`, `activity.delta`

The current transport adapter maps the live `POST /v1/agui` SSE stream into those events. The UI uses Harness REST resources directly for authentication, Agent catalog, thread history, context windows, approvals, input artifacts, generated artifacts, archiving, cancellation, and context rebase.

This separation is deliberate. AG-UI can later be replaced with a WebSocket, a native Harness event stream, or another Responses-compatible BFF without changing chat components or their state reducer. OpenAI Responses semantics belong between Harness and the model provider; they are not exposed as the browser contract.

## Recovery

The initial POST response supplies `X-Harness-Run-ID`. The client records each SSE `id`. If the initial stream ends before a terminal event, it repeatedly calls `/v1/agui/runs/{run_id}/events` with `Last-Event-ID` until it receives `run.finished` or `run.error`. Cancellation uses the server run ID when available.
