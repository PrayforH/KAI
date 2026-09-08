# Changelog

All notable product changes are recorded here. Versions follow Semantic Versioning; signed release
manifests remain the authority for exact source commits, image digests, SBOMs and Agent bundle hashes.

## [0.3.0] - 2026-09-08

### Added

- Agent Builder converges to a five-stage authoring spine — goal & contract, capabilities,
  behavior, trial run, publish — with per-stage completion state and blocking reasons surfaced
  in the main editor area instead of a separate lifecycle bar.
- Agent Copilot minimal loop: a review-only Builder patch (task contract, system prompt and
  baseline eval cases) generated from the governed task contract and applied per accepted
  block; nothing bypasses release gates.
- Server capability-catalog templates join the new-Agent flow; template scaffolding is used
  as-is and is no longer overwritten by client defaults.
- Managed video generation modes (interactive H3, image-guided) and explicit Ref2VA generation
  in the task composer.
- Personal deliverables index, protected Agent deletion and refined conversation feedback in
  the web console.
- Monochrome "KAI WORKBENCH" task workspace: black/white/gray Codex-style theme, left-aligned
  collapsible sidebar with project groups, a right-hand context rail (task/file/agent/model/run
  info) toggled from the header, compact task rows with unread and active dots, and a ghost
  composer with up-arrow send.
- Case-level Eval result analysis in Evaluate & Operate; question banks import as durable
  dataset versions.
- Long-term memory v2: hybrid keyword + semantic recall backed by pgvector inside the existing
  PostgreSQL instance, `search_memory`/`read_memory`/`propose_memory` tools on both Claude and
  Codex runtimes, and post-run extraction of preferences, project facts, entities and decisions
  into confirmable candidates with versioned replace semantics.
- Run steering for in-flight user guidance, and built-in public-web `WebSearch`/`WebFetch`
  capabilities (Claude SDK) selectable per Agent without an MCP connection.
- Unified Builder workbench entry: create and edit share one build workspace with an assets
  rail, builder conversation with block-level materials, a platform skills catalog with
  in-product skill authoring, and streaming try-run.
- NexAU Agent package import (ZIP/RAR4/RAR5 via sandboxed in-memory libarchive extraction) with
  root-Agent detection and inline `system_prompt` support.
- Cross-device task read-state sync through the server (`PUT /v1/agui/threads/{id}/read`),
  replacing localStorage as the source of truth; task groups collapse beyond five rows with
  expand/collapse controls.
- Image inputs in the build workbench: vision-capable model selection when the registered model
  lacks vision, and shared attachment thumbnails with original-image preview before and after send.

### Changed

- Runtime selection and Codex compatibility copy are driven by server RuntimeCapabilities
  (model API formats, limitations); frontend hardcoding of runtime capability judgments is
  removed and covered by a cross-layer contract fixture shared with compiler tests.
- Knowledge-type MCP entries are grouped as the fact plane inside the capabilities stage,
  separate from the action-plane tool list.
- The default analyst/operator scaffolds reflect the expanded builtin tool set and
  production-standard policy defaults.
- The system default lead-agent lists its tasks directly under Tasks; business agents keep
  grouped display, and the account menu is trimmed to settings and logout.

### Fixed

- Codex runtime isolation: sandbox enabled inside docker workers, docker workspace default for
  Studio agents, clearer thread startup failures and long-running Codex loop support.
- Anthropic-compatible direct endpoints now receive the required `/v1` prefix exactly once;
  `glm-5-3-flash` is registered as a vision model and GLM-5.2 is removed from seeded catalogs.
- Build workbench layout: wider builder/asset panels, composer columns keep attachments inside
  their own pane, and processing progress renders above the answer and collapses after success.

## [0.2.0] - 2026-08-09

### Added

- Agent Studio authoring, immutable Agent bundles, Eval/quality gates and test → canary → production
  promotion with snapshot and image rollback.
- Durable Sessions, Runs, workspaces, artifacts, approvals, memory, team-space ACLs and shared drafts.
- Context-window telemetry, SDK-native compaction lifecycle events, structured Session Digests and
  recovery/rebase APIs without rewriting opaque SDK transcripts.
- PostgreSQL-authoritative event streaming with Redis wakeups and strict cancellation convergence.
- Web console for chat, Studio, execution details, artifacts, workspace access and context recovery.
- Claude official routes surface the SDK `auto` permission mode accurately: routine workspace work
  proceeds automatically while Harness policy still rejects or confirms high-risk boundaries.
- Built-in policy and operator-template copy is upgraded idempotently from exact historical defaults,
  including tenant-managed catalogs, without replacing tenant-authored capabilities or ownership.
- Collaboration-space creation now uses the same accessible right-side authoring pattern as MCP and
  knowledge connections, with focus containment, responsive layout and inline failure recovery.
- Environment policy no longer exposes the disconnected manual Knowledge ID field: external
  knowledge follows the registered MCP boundary, while legacy policy references remain read-only
  and are preserved when other environment settings are saved.
- Studio keeps durable runtime-quality enforcement in the release gate but removes the duplicate
  operations dashboard from the authoring journey, eliminating Score, Incident and Rule reads that
  did not help creators configure, test or publish an Agent.
- The illustrated Feishu product manual is synchronized with the gray31 authoring flow, including
  the four release gates, unsaved-change protection, collaboration-space drawer and MCP-governed
  knowledge boundary while preserving all ten product screenshots.
- The task header no longer exposes a global Langfuse operations link; exact Trace access remains
  available in the corresponding run details. The account menu now links to the illustrated product
  manual, and its collapsed mobile-rail popover stays above the sticky task composer.
- The compact task header gives the context/recovery trigger a stable accessible name, and its panel
  now shares the product dialog focus contract: deterministic entry focus, Tab containment, Escape
  close and focus restoration to the invoking control.
- Studio's runtime-contract and immutable-version drawers now share that same dialog contract,
  including transition-safe initial focus and focus restoration. The task welcome surface also uses
  a true page-level heading without changing its visual hierarchy.
- Creating another personal Agent from a dirty Studio draft now uses an explicit save-then-create
  transaction: cancellation preserves the editor, save failures or revision conflicts never replace
  the current draft, and only a successful save opens the new unsaved Agent. Fresh drafts now report
  “尚未保存” instead of the misleading “已同步 r0”.
- Switching between existing personal Agents now follows the same save-first transaction: the active
  row cannot reload over its own dirty editor, cancel returns focus without losing input, and save
  failure or revision conflict prevents the switch. Concurrent switch requests are serialized and
  the target row reports progress while its draft is loading.
- Resolving a Studio revision conflict no longer lets “load latest” silently overwrite local work.
  Loading the control-plane revision now requires an explicit destructive confirmation, serializes
  repeated requests, shows progress, and preserves the local editor when the reload request fails.
- The task Agent switcher now closes on Escape, outside pointer interaction and focus departure while
  restoring focus to its trigger. An active task pins its current Agent version until completion or
  stop, but choosing a different Agent still starts an isolated new task instead of mutating the run.
- Starting another task no longer strands an unsent composer draft behind an invisible empty thread.
  Empty task shells are reused and refocused, durable tasks still create isolated threads, and an
  indeterminate history read fails closed without mutating a possibly durable task.
- Run details are mounted back into the task workspace and opened from the corresponding execution
  summary. Langfuse access is therefore exact-run scoped through that drawer instead of returning as
  a context-free operations link, and historical runs retain their own Run and Trace identifiers.
- The retired `anthropic-official` route is removed from Studio, task model selection, system- and
  tenant-managed catalogs, and new deployment examples. Rolling Web instances suppress stale catalog
  projections, while the protocol adapter remains available only to execute already immutable historical
  versions.
- Unified dark/light task navigation with readable search, empty, loading and recovery states across
  desktop and compact modal-drawer layouts.
- Docker Compose and Kubernetes/gVisor deployment baselines with non-root runtime images.

### Security

- Digest-pinned base images, signed kubectl source verification, exact image vulnerability gates,
  SPDX SBOMs, OpenVEX, BuildKit provenance and keyless Sigstore release signatures.
- Version-bound Release Notes and canonical source-timestamped evidence archives with exact checksums
  and signatures; GitHub Releases remain draft until protected production acceptance succeeds.
- Tenant/user ownership enforcement, service-owned credential isolation, prompt-injection trust
  propagation, secret redaction and build-free digest promotion.

### Known boundaries

- Formal production promotion requires configured protected GitHub environments and a self-hosted
  deployment runner; local dirty-worktree images are gray evidence only.
- Custom roles are intentionally excluded from 0.2.0; audited built-in member/admin/service roles are
  the supported authorization model.

## [0.1.0] - 2026-08-06

### Added

- Initial Claude Agent SDK platform baseline with FastAPI control plane, Worker execution plane,
  immutable Agent bundles, approval lifecycle, Eval gates, Docker Compose and Web console.
- Initial GitHub Release and tag. Its container-security workflow failed before build-once/signing, so
  it does not provide signed image, SBOM or promotion evidence and must not be reused or retagged.
