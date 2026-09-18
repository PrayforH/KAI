# 174 DeepAgents code view release — 2026-09-14

## Scope

Source commit: `ce8a6d700b12d304a38023f1125a877ea5417ec7` on `develop`.

Agent assets now open a read-only DeepAgents source workspace. It uses Codex Dark / Light TextMate themes with `@pierre/diffs` 1.4.2 and `@pierre/trees` 1.0.0-beta.6, system fonts, line numbers, colored file icons, searchable folders, file navigation, copy, wrapping and project download. The theme can follow the UI or be selected and remembered independently. The viewer loads only when opened.

The source API reads the same generated ZIP as project export. Requests include the saved draft revision; stale revisions return 409 and other owners cannot read the draft. Binary and oversized files remain downloadable, with a 256 KiB per-file and 2 MiB aggregate text-preview limit. Unsaved configuration is explicitly distinguished from the displayed revision. This does not add editing or source-to-draft synchronization.

## Validation

- Backend source and Studio API tests: 57 passed.
- Frontend suite: 593 passed, 1 skipped; production build passed.
- Browser: first load, Codex Dark / Light, Python / JSON highlighting, file filtering and selection, wrapping, returning to configuration, and 390px layout verified.
- 174 candidate and production API: source / ZIP file contents and digest match; stale revision and owner isolation checks pass. Temporary validation draft removed.
- Existing knowledge-base and model APIs remain available; DeepSeek-V4.1-Flash retains vision capability.

## Release

Images: `kai/axis-api:develop-20260914-ce8a6d7` and `kai/axis-web:develop-20260914-ce8a6d7`.

Release directory: `/data/kai-develop-20260914-code-view`. It contains the pre-release database dump, private container/environment snapshots, the compose overlay, image metadata and rollback scripts. Existing deployment overlay and application environment values are preserved. No database migration is required.

The previous web container `axis-web-develop-20260914-tools-vision` is retained for rollback. The new production web container is `axis-web-develop-20260914-code-view`, serving port 3501.

Deployment completed. API, three workers, quality sync and the production web container are healthy, with zero restarts. The external production URL `http://172.20.109.174:3501` returns 200; all 16 entry JavaScript chunks and the code/theme chunk load. The temporary preview route is absent from the production bundle, and validation containers/drafts have been removed.

Image IDs:

- API: `sha256:9cacffb54404d7a99de6ae8e43552453cb4b6a214a44654269557164cf8d19a5`
- Web: `sha256:4426faed26afa49f698df54224d75d37525719b6693fa378ce5021d403f914d9`
