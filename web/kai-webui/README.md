# KAI WebUI — Harness integration

A focused KAI frontend derived from the open-source DeepSeek Harness UI. It
intentionally excludes terminal and local-workspace features.

Version `0.6.2-flash-search.1` uses a compact ZCode-inspired project/task sidebar,
a centered multi-resource search palette, and the enabled `deepseek-v4-flash`
route by default. It
keeps the frontend-owned KAI Web Session
Protocol. AG-UI is an interchangeable live-stream adapter; authentication,
Agent discovery, thread history, context, approvals, input files, generated
artifacts, cancellation, and archiving use Harness resources directly. See
[`docs/protocol.md`](docs/protocol.md).

```bash
npm install
npm run build
```

The production Nginx image proxies `/api/` to the KAI backend on the 174
validation environment. Change `nginx.conf` before promoting to another host.
