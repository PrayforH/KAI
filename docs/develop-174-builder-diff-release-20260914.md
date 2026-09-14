# 174 builder revision diff release — 2026-09-14

Source commit: `f6225d51ad5e1dce1ed01512c435aec3b3319eef` on develop.

## Changes

- Code view follows the application color mode; removed the independent theme selector and persisted override.
- Text preview limits: 10 MiB per file, 50 MiB total. Virtualized source rendering handles long files without mounting every line.
- Builder proposals can preview actual DeepAgents file differences. Applying a proposal captures the before/after comparison and opens a unified diff with file status, line additions/deletions and expandable unchanged context. A rerun continues to open the test panel instead.
- Comparison is read-only and validates draft revision and edit permission. Applying still uses the original revision CAS endpoint. Pending previews cannot download an unapplied project.
- Comparison covers the most recent change in the current workbench session; this does not add persistent arbitrary draft revision history.
- Builder capability assessment: `docs/builder-capability-scope-20260914.md`. No expansion of builder management permissions in this release.

## Verification

- Backend related suite: 69 passed; follow-up streaming digest tests: 4 passed.
- Frontend: 596 passed, 1 skipped. Production build, Ruff and targeted Pyright passed.
- Browser: dark/light following, no theme selector, unified Skill diff, pending/applied status, file filtering and a ~1.8 MB / 25,000-line preview with bounded rendered rows.
- Candidate and production API: 1,215,000-byte Skill reference returned completely; preview does not mutate the draft; applied export files match comparison output; other owner rejected and stale revision returns 409. Temporary drafts removed.
- Production web: external port 3501 returns 200; 16 entry chunks and code/theme chunk load; builder diff endpoint is present in the client; removed theme override and temporary preview route are absent.
- API, three workers, quality sync and production web are healthy with zero restarts. Validation containers removed.

## Deployment and rollback

Release directory: `/data/kai-develop-20260914-code-diff`, containing the pre-release database dump, private environment/container snapshots, preserved compose overlay chain, image metadata and rollback scripts. No migration is needed. Existing application settings are preserved.

Images:

- API: `kai/axis-api:develop-20260914-f6225d5`, image `sha256:3c2c392bc4779abb9735d947144f423a80fe0bec90770423d179d2d819bb2349`.
- Web: `kai/axis-web:develop-20260914-f6225d5`, image `sha256:7bfdbe148a2889255a430e7e11893af77c057ef63a768555cd80eb6f0471044d`.

New web container: `axis-web-develop-20260914-code-diff`. Previous `axis-web-develop-20260914-code-view` is stopped and retained for rollback.
