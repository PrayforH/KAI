# Skill object storage and runtime cache on 173

The Builder previously embedded Skill instructions and attachment contents in every
PostgreSQL draft, draft revision and published/validated Agent version. Runtime
staging decoded and uploaded the same assets for each Run, even inside a reused
CubeSandbox instance.

## Storage and compatibility

- `SkillBlobStore` moves Skill bodies (instructions and files) to immutable,
  SHA-256-addressed, gzip-compressed MinIO objects under
  `<tenant>/skill-blobs/v1/<digest>.json.gz`. Names, descriptions, provenance and
  runtime content hashes stay in their existing database metadata fields.
- Repositories hydrate references after their existing tenant/owner lookup.
  Domain models, API payloads, edits, exports and runtime snapshot hashes are
  unchanged. Old inline rows remain readable; changed Skill content gets a new
  reference. Card/catalog projections need no object download.
- Upload completes before the database commits a reference. Missing or corrupt
  referenced objects fail closed. Process caches are tenant-keyed, integrity
  checked and bounded to 64 MiB of compressed bytes.
- `python -m harness.storage.migrate_skill_blobs` converts existing drafts,
  revisions and Agent versions, locking and committing one row at a time after
  exact round-trip comparison. A second invocation is a no-op. `--inline`
  reconstructs the prior persisted representation for rollback. Revision numbers,
  timestamps and version identity do not change.
- No automatic object deletion is introduced: historical revisions may still
  refer to the same immutable object. Keep object storage in database backup and
  recovery procedures.

## Runtime staging

- Worker-local decoded Skill file cache is content addressed, verified and capped
  at 256 MiB. Workspace files are independent copies, never hard links.
- CubeSandbox/E2B caches verified Skill bytes outside the per-Run workspace.
  Preparation copies cache hits locally, uploads misses in bounded multipart
  batches, and uploads only new hashes after edits. Skill subdirectories are
  created remotely in batches instead of per-directory API requests.
- Only `.claude/skills` and `.agents/skills` enter this cache. Inputs, credentials
  and outputs retain their ordinary transfer and lifecycle behavior.
- Cache corruption is repaired from verified source bytes. An unavailable remote
  cache falls back to complete Skill upload. Cache contents are bounded to
  256 MiB; sandbox TTL/destruction also removes them. A new sandbox still needs
  an initial transfer; this does not eliminate sandbox/model startup time.

## Verification

- Backend regression: 959 tests passed across storage, core, sandbox, runtime,
  Studio, worker and production/runtime composition.
- Final targeted sandbox/cache/API checks: 123 passed, including cache failure
  fallback, changed content, corruption repair and unchanged-byte reuse.
- PostgreSQL: 10 passed in an isolated temporary database; reference persistence,
  old inline reads, history, CAS conflict, owner isolation, catalog projection,
  process restart and conversion back to inline covered. Test database removed.
- Ruff passed. New storage/cache modules, draft repository and manifest pass
  strict Pyright with the project interpreter. The existing E2B log parser has
  eight pre-existing unknown-type diagnostics outside this change.
- Live CubeSandbox test with an existing 219-file / 8,338,435-byte Skill:
  cold preparation 1.157 s / 8,333,002 bytes uploaded (duplicate content deduped);
  warm preparation 0.524 s / 0 bytes; one changed file 0.473 s / 15,596 bytes.
  These are Skill staging measurements, not full model-turn latency. The
  dedicated QA sandbox was destroyed after verification.

## Deployment and rollback

API and Worker image: `kai/axis-api:evolution-builder-2dad60f6`.
Web remains `kai/axis-web:evolution-builder-21ec4575`.
Backup directory on 173:
`/data/agent-studio-evolution-20260920/backups/builder-skills-2dad60f6/`.
It contains a custom-format PostgreSQL dump of the three affected tables, the
previous compose configuration and available source revision files.

Both API and Worker must support references before enabling writes. To roll back,
stop those services, run the **new image's** migration command with `--inline`
using a one-off compose container, then restore the backed-up compose/source
revision files and start API/Worker. Do not start old readers against references.
MinIO objects need not be removed for rollback.

Live migration converted 4 drafts, 8 historical revisions and 17 Agent versions.
The second migration reported zero changed rows in all three tables. Total JSON
payload bytes (not physical PostgreSQL file size) changed as follows:

| Table | Rows | Before | After |
| --- | ---: | ---: | ---: |
| agent_drafts | 14 | 24,059,047 | 110,648 |
| agent_draft_revisions | 9 | 34,972,086 | 133,370 |
| agent_versions | 48 | 65,451,219 | 336,380 |

After deployment, all 14 drafts, 9 saved revisions and 48 versions passed domain
validation. All 33 referenced Skill objects were read with a fresh MinIO cache;
87 version-pinned Skills materialized successfully. Catalog and draft-history
reads passed. The 262-file draft's full export still returned 8,710,262 bytes,
with directory metadata 56,718 bytes and selected-file response 2,813 bytes;
owner isolation, stale-revision rejection and path isolation passed. API, Worker
and Web health checks were healthy and the Web login route returned HTTP 200.
