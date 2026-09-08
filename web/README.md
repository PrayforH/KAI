# Web applications

The primary Codex workbench candidate is `codex-webui`, imported from
`LimLLL/codex-webui` as a pinned ZIP snapshot. Its exact upstream revision and
archive checksum are recorded in `codex-webui.UPSTREAM.md`.

- `codex-webui`: primary WebUI trial; Codex app-server workbench.
- `codex-web`: retained alternative snapshot from `wangru8080/codex-web`.
- `harness-console`: legacy AXIS console; no longer the target for generic
  Codex chat/workbench polishing.

AXIS remains the control plane for tenant policy, agent definitions, versions,
evaluations, deployment, audit, and durable artifacts. The selected Codex WebUI
is the interactive workbench and should connect through server-side integration
rather than duplicating those control-plane responsibilities.
