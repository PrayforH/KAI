# Consolidate presentation fixes for develop and the 79 deployment

## Scope

Based on `develop@e6dcfade`, collect the completed non-project UI fixes from
`auto/d1-ui-intent-20261014` and `fix/ui-automation-presentation-access`:

- Keep malformed conversational forms readable instead of exposing protocol code blocks.
- Preserve the deployed map/kitchen assets and their expandable HTML preview/menu.
- Paginate conversational choices without truncating selected values; retain the sapling preview
  brand already deployed to the target environment.
- Cover scrolling content beneath the sticky composer while keeping its menus usable.
- Restore automation-thread presentation reads through the existing bound-thread authorization;
  preserve owner-only write/cancel permissions and cross-user/cross-tenant rejection.

Exclude project-space branches (`auto/zcode-projects`, `auto/zcode-projects-174`), the Qwen trial,
run-dispatch redesign and dependency-update PRs. There are no project-home resources, project API,
project migrations, or project-specific composer/layout changes in this integration.
The kitchen static asset keeps SHA256
`3e32ac3dab07639edf4d90bb83c636676e4b94b5c1c77a47b5699b22bf0a5e9f`.

The requested integration branch is **develop**, and the requested application deployment target
is **172.20.105.79**. Keep 174 available for project-space iteration; do not replace its application
images with this integration.

## FunASR restoration on 174

Restored only `HARNESS_DICTATION_REALTIME_URL` to `ws://172.20.109.229:18013` in the authoritative
`/data/kai-intelligent-ui-develop-b6289d48/compose.runtime.private.json`.
The currently deployed project-space API image `kai/projects-api:projects-20261018-r9` was preserved.
The restoration script verified the canonical configuration matched the live API and that the
rendered environment changed only that URL. It did **not** restore the older full Qwen-trial
configuration, which would have reverted the ongoing project-space work.

Before restarting the API there were zero established dictation connections. FunASR start/stop
and a public Chinese audio replay passed after cutover; a draft appeared before STOP and the final
text was `甚至出现交易几乎停滞的情况。`. API health returned 200. The Qwen trial sidecar had zero
active sessions and was stopped after the FunASR check. Application rollback configuration is
retained privately under `/data/kai-funasr-restore-20261018`.

## Validation and 79 status

- Local targeted backend suite: 204 passed, including presentation reads, actions, HTML,
  conversation-input parsing, artifacts and owner/tenant isolation; Ruff passed.
- The frontend full suite passed: 191 files, 1209 tests passed, 1 skipped. TypeScript and the
  production build passed from an isolated source copy using npm 11.6.2 and the domestic registry.
- Initial CI identified five new type diagnostics in test fixtures. Typed the pagination fixture
  and injected an explicit in-memory binding repository into the authorization test; no type
  baseline or product behavior was changed. The final commit must pass all required CI checks.
- The existing 79 HTTPS entry responds 200 with its known certificate and title `sapling preview`.
- At preparation time, 79 rejected the available SSH key and SSH from 174 was refused. The user
  has been asked for an authorized SSH connection. No 79 application deployment is claimed yet.
- Before deploying, inspect the actual 79 API/Worker/Web baselines and preserve its existing map,
  scene and environment-specific settings. Deployment must not overwrite unrelated application
  code, credentials, user data or other services, and must retain an exact rollback configuration.
