# Release 0.2.0 checklist

This checklist specializes [release-promotion.md](release-promotion.md) for the P0/P1 release candidate.
The public `v0.1.0` tag is historical and immutable; its failed signing workflow is not 0.2.0 evidence.

## Candidate gates

- [x] Python, Web, Helm and Changelog all declare `0.2.0`.
- [x] `harness.release/v2` binds platform version, source commit, Agent bundles, image digests and SBOMs.
- [x] Dated Release Notes are hash-bound to the signed manifest; the workflow rejects `Unreleased`.
- [x] Evidence archive has canonical ordering/source timestamp and receives its own exact checksum and Sigstore bundle.
- [x] GitHub Release stays draft until production promotion succeeds.
- [x] Legacy v1 manifests remain readable for rollback, but cannot be newly promoted.
- [x] `fb472b2` workspace/shared-draft boundary is an ancestor of the P1 source baseline.
- [x] 174 gray2 deploy, migration, health, chat, cancel and workspace isolation evidence retained.
- [x] Exact gray2 API/Web and candidate Sandbox scans have zero HIGH/CRITICAL findings.
- [x] Candidate-tree `make verify` is strict green: Pyright 0, 1058 passed / 4 skipped; Alembic head 0029.
- [x] One-command local verification derives only test Postgres/Redis/MinIO settings from Compose, creates the isolated test DB idempotently and does not import runtime model, MCP, Langfuse or telemetry settings.
- [x] Fault-injected apply preserves the failed candidate manifest and restores the prior digest without database downgrade or seed.
- [x] Deployment rejects a Compose secret env file that is readable by group or others.
- [x] Promotion attempts use run/attempt-scoped idempotency; checkpoint plans survive partial failure and repeated rollback cannot reverse direction.
- [x] Bundle publication, promotion, rollback and smoke chat use one dedicated release identity; success re-verifies the durable healthy snapshot pointer.
- [x] Production rejects an untagged/manual candidate before any image deployment or migration; the exact tag draft is checked again before publication.
- [x] Promotion performs a fresh Harbor robot login before private image signature, provenance and digest-pull checks.
- [x] Candidate-tree Web gate is green: 57 files / 367 tests and 19-page production build.
- [x] Personal Agent immutable history/current-pointer rollback passed API, migration, desktop, dark/light and 390px acceptance; existing Sessions remain version-pinned.
- [x] Native browser confirms are replaced across Studio, memory and data lifecycle; the shared alert dialog passed cancel-first focus, Escape/Tab, focus restoration, dark/light and 390px acceptance.
- [x] MCP/knowledge authoring drawers, catalog-sync notice and resource-delete dialog share focus entry, Tab loop, Escape and invoking-control restoration; MCP/knowledge passed 390px production-browser acceptance.
- [x] Studio dirty drafts guard current-tab navigation and browser unload; save-and-leave only navigates after a successful save, while cancel retains the edit and save conflict/failure retains the page.
- [x] Studio dirty drafts also guard browser Back history traversal; cancel retains the edit, confirmed save removes the same-URL sentinel before navigation, and the original Next.js history state is restored intact.
- [x] Compact task navigation reacts to breakpoint changes and behaves as a modal drawer with scrim, Escape/focus containment, close-after-selection, correct stacking above the main toolbar and zero 390px overflow; desktop keeps the 248px sidebar contract.
- [x] 174 Web route and browser acceptance target Agent Studio on Compose port `3301`; port `8180` belongs to the colocated WeKnora reference system and is not release evidence.
- [x] Studio-to-task catalog reads use native lightweight version projections; 174 authenticated latency is 27ms median / 43ms P95 over 30 warmed samples, and migration 0029 preserves old-image writes throughout the rollback window.
- [x] Task directory reads batch Session resolution and use adaptive polling: gray25 is 45ms median / 66ms P95 with 33 tasks, active work remains at 4s, idle work backs off to 30s, hidden tabs pause and focus refreshes immediately.
- [x] Task search and empty/loading/error states use the shared dark/light theme contract; gray26 passed desktop and 390px browser acceptance with zero horizontal overflow and WCAG AA text contrast in both modes.
- [x] Model policy exposes Harness policy plus SDK Auto semantics consistently; exact legacy default copy migrates idempotently in system and tenant-managed catalogs without changing tenant-authored capabilities or ownership. Gray28 preserved `updatedBy`, incremented the live catalog exactly once and passed 174 health/log/HTTP verification; gray42 later retired the Anthropic official product route.
- [x] Collaboration-space creation uses the same right-side authoring contract as MCP/knowledge: inline error retention, cancel-safe submission, focus entry/loop/restoration, Escape/scrim close, dark/light and 390px zero-overflow acceptance. Gray29 passed 174 health/log/route and exact bundle verification.
- [x] Environment resource policy removes the disconnected manual Knowledge ID input and follows the registered MCP boundary; legacy references remain read-only and survive unrelated saves. Gray30 passed dark/light, 390px zero-overflow, Colima restart E2E and 174 exact-bundle/health/log verification.
- [x] Studio authoring keeps the durable runtime-quality release gate but removes the duplicate Score/Incident/Rule operations panel and its three nonessential reads. Gray31 passed dark/light, 390px zero-overflow, Colima restart E2E and 174 new/removed-bundle marker verification.
- [x] Task authoring removes the context-free global Langfuse operations link while retaining exact Trace access in run details; the shared account menu exposes the illustrated manual, and its collapsed 390px popover stays clickable above the sticky composer. Gray32 passed full gates, dark/light/mobile browser acceptance, Colima restart E2E and 174 digest/health/route/log verification.
- [x] The compact context/recovery trigger keeps a stable accessible name when visible text is hidden, and its panel shares deterministic focus entry, Tab containment, Escape close and invoking-control restoration. Gray33 passed 390px/desktop production-browser acceptance, 357 Web tests, Colima restart E2E and 174 digest/health/bundle/log verification.
- [x] Studio runtime-contract and immutable-version drawers share the same focus-entry, Tab, Escape and return-focus contract; transition visibility no longer defeats initial focus, and the task welcome has a single page-level heading. Gray34 passed dark/light, 390px/desktop production-browser acceptance, full gates, Colima restart E2E and 174 digest/health/bundle/log verification.
- [x] Creating a personal Agent from a dirty Studio draft is save-then-create: cancel preserves the editor, save failure/conflict cannot replace it, and a successful save reserves the prior name before opening the new unsaved draft. Gray35 passed dark/light, 390px/desktop browser acceptance, 358 Web tests, Colima restart E2E and 174 digest/health/bundle/log verification.
- [x] Switching existing personal Agents is save-then-switch: the active row cannot reload its own dirty editor, cancel preserves input and restores focus, successful save precedes loading the target, and failure/conflict keeps the current editor. Gray36 passed dark/light, 390px/desktop browser acceptance, 359 Web tests, Colima restart E2E and 174 digest/health/bundle/log verification.
- [x] Reloading a conflicted Studio draft is an explicit destructive transaction: cancel preserves local input, confirmation loads the exact control-plane revision, concurrent reloads are serialized, and API failure retains the conflict and editor. Gray37 passed dark/light, 390px/desktop browser acceptance, 360 Web tests, real API-outage recovery, Colima restart E2E and 174 digest/health/bundle/log verification.
- [x] The task Agent switcher closes on Escape, outside interaction and focus departure with trigger-focus restoration; queued/running/approval-waiting tasks pin their current Agent version while another Agent still creates a new isolated task. Gray38 passed dark/light, 390px/desktop browser acceptance, a real running-task lock transition, 362 Web tests, Colima restart E2E and 174 digest/health/bundle/log verification.
- [x] Empty task shells reuse the current composer and preserve unsent drafts; only durable tasks create a new isolated thread, while an unknown history state performs no destructive mutation. Gray39 passed dark/light, 390px/desktop browser acceptance, a real durable-task transition, 366 Web tests, Colima restart E2E and 174 exact digest/health/route/log verification.
- [x] Every execution summary exposes its own “运行详情” control and the mounted drawer receives that exact run snapshot; Langfuse Trace access remains run-scoped rather than returning to global navigation. The final combined candidate passed 367 Web tests, desktop and 390px dark/light acceptance, responsive drawer contract checks and Colima restart E2E; gray40 was superseded before deployment.
- [x] `anthropic-official` is absent from Studio defaults, task selection, environment defaults and both system-/tenant-managed live catalogs; historical immutable versions retain protocol compatibility only. Gray42 passed 1060 Python tests, 367 Web tests, exact route/digest/architecture/revision checks, six-route HTTP acceptance and zero severe application logs on 174, with gray41 retained as the direct rollback target.
- [x] Remote CI test-DB/quota and knowledge-owner fixes absorbed without accepting its type-error waiver.
- [ ] Repeat the same gates from the final clean commit and retain the workflow URL.
- [ ] Full P0/P1 changes reviewed and committed; no generated secrets or local environment files staged.

## Build once and sign

- [ ] Push the immutable annotated `v0.2.0` tag; never move `v0.1.0`.
- [ ] Release workflow quality, container-security and build-once jobs all succeed.
- [ ] Record workflow URL, source commit, releaseId, three image digests and Agent bundle hashes.
- [ ] Verify image signatures, detached SBOM/VEX signatures, attestations and BuildKit provenance.
- [ ] Verify the draft Release evidence archive signature before environment promotion.
- [ ] Copy evidence that must outlive the 90-day artifact retention to approved immutable storage.

## Harbor and promotion infrastructure

- [ ] Configure trusted bridge runner label in `HARNESS_RELEASE_RUNNER`.
- [ ] Configure `HARNESS_RELEASE_REGISTRY=harbor.shdata.com:5000`.
- [ ] Configure `HARNESS_RELEASE_NAMESPACE=agent-studio/amd64` and least-privilege robot credentials.
- [ ] Install the Harbor CA for Docker/BuildKit/Cosign; do not disable TLS verification in workflows.
- [ ] Create protected `test`, `canary`, `production` GitHub environments and required variables/secrets.
- [ ] Register a `self-hosted,linux,harness-deploy` runner that reaches GitHub, Harbor and target Docker.
- [ ] Run `make release-infra`; retain the redacted JSON result and require `"ready":true` before tagging.

## Ordered acceptance

- [ ] Promote the same releaseId/digests to test and retain real AG-UI marker evidence.
- [ ] Promote to canary after test; run the deliberately failing canary and prove image/Agent rollback.
- [ ] Complete desktop and mobile browser acceptance against canary.
- [ ] Promote to protected production after approval; prove health, logs and N-1 rollback window.
- [ ] Confirm the post-production job published the same draft as the public `v0.2.0` Release.
